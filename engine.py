"""Task orchestration. Only the executor may dispatch an external write."""

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path

import agents
import app_mcp
import bugflow
import orchestrator
import selfcheck
import sync as instances
import vps_setup
import workscope
import github_signin
import connectors
import workspace_bridge
import google_workspace
from signin import SignIns
from suggestions import Suggestions
from context import Context
import toolchains
import worker
from store import Store, uid


# The GitHub CLI login of a gh_account connection, copied into the app's store so peers can use it.
GITHUB_CLI_LOGIN = connectors.GITHUB_CLI_LOGIN


class SyncedSecrets:
    """The app's own credential store, as seen by sync. Logins held by other tools are not listed."""

    @staticmethod
    def identities(db):
        found = []
        for c in db.rows("connections"):
            if c.get("auth_method") == "gh_account" and c.get("identity"):
                found.append(GITHUB_CLI_LOGIN + c["identity"].casefold())  # Shared by share_github_logins.
                continue
            if c.get("workspace_profile") or c.get("auth_method") in {"gh_account", "gh_repository"}:
                continue
            if c.get("github_parent"):
                continue
            found.append(c.get("credential_key", c["id"]))
        found += [entry["credential"] for entry in db.rows("auth_clients") if entry.get("credential")]
        return list(dict.fromkeys(found))

    @staticmethod
    def read(identity):
        return connectors._secret(identity)

    @staticmethod
    def store(identity, secret):
        connectors._store().access("store", identity, secret)

    @staticmethod
    def delete(identity):
        connectors._store().access("delete", identity)


def digest(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def required(data, key, limit=100000):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"Enter a valid {key}.")
    return value.strip()


BASE_SKILL = "Discover explicit requests, assigned reviews, open promises and unanswered questions. Skip work someone else has claimed. Explain the evidence."
TERMINAL = {"closed", "dismissed", "skipped", "done"}
# An owner silent this long is taken over automatically. See "Automatic takeover" in instances-and-sync.md.
TAKEOVER_SECONDS = 900
BUSY = {"sending", "uncertain"}
# A run that failed because a deleted app build took its files with it. See desktop-runtime.md.
MISSING_APP_FILE = re.compile(r"No such file or directory: '[^']*\.app/Contents/")


class Engine:
    def __init__(self, root, clock=time.time):
        self.db = Store(root)
        # Pairing, the synced log and item ownership. See docs/specs/instances-and-sync.md.
        self.instances = instances.Sync(self.db, clock, secrets_api=SyncedSecrets())
        self.instances.package_hook = self.package
        self.instances.accept_hook = self.accept
        connectors.secret_listener = self.instances.record_secret
        self.signins = SignIns(self)
        self.suggestions = Suggestions(self)
        self.context = Context(self)
        self.now = clock
        self.wake = threading.Event()
        self.repository_recheck = threading.Event()
        self.repository_recheck.set()
        self.next_repository_probe = 0
        self.stop = threading.Event()
        self.jobs = {}
        self.event_locks = [threading.RLock() for _ in range(32)]
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="task")
        self.sync_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="source")
        self.dispatch_lock = threading.RLock()
        self.absence = None
        self.recent_offers = {}
        self.origin_cache = {}
        self.next_github_share = 0
        self.clone_lock = threading.Lock()
        self.toolchain_lock = threading.Lock()  # One toolchain install at a time on this instance.  # One clone at a time; a waiting caller then reuses the finished one.
        self.next_balance = 0
        # The app MCP server. The HTTP service sets mcp_url once it listens.
        self.mcp_sessions = app_mcp.Sessions()
        self.mcp_tools = app_mcp.Tools(self)
        self.mcp_url = None
        self.agents = agents.Agents(root, self.settings)
        self.orchestrator = orchestrator.Orchestrator(self)
        # Guided setup and VPS setup over SSH. See docs/specs/guided-setup.md.
        self.vps = vps_setup.VpsSetup(self.instances, self.settings, self.db.log,
                                      Path(__file__).resolve().parent, open_terminal=agents.open_in_terminal,
                                      on_done=self.remember_server)
        self.gh_login_cache = (0.0, None)
        with self.db.atomic():
            settings = self.db.get("config", "settings")
            if not settings:
                self.db.put("config", {"id": "settings", "dry": True, "provider": worker.default_provider()})
                self.skill("workflow", BASE_SKILL, True)
            elif "provider" not in settings:
                settings.pop("model", None)
                settings["provider"] = worker.default_provider()
                self.db.put("config", settings)
                self.db.log("Settings upgraded", f"AI CLI: {settings['provider']}")
            self.global_project = self.initialize_projects()
            self.scope = workscope.WorkScope(self.db)
            for connection in self.db.rows("connections"):
                if (
                    connection.get("auth_method") == "slack"
                    and connection.get("identity")
                    and not self.db.get("connections", "slack-workspace:" + connection["identity"])
                ):
                    self.signins.workspace(
                        connection["identity"],
                        connection.get("credential_key", connection["id"]),
                        connection["identity"],
                    )
                if "project" in connection:
                    del connection["project"]
                    self.db.put("connections", connection)
            for t in self.db.rows("tasks"):
                if t["status"] in {"working", "sending"}:
                    t["status"] = "uncertain" if t["status"] == "sending" else "queued"
                    self.save(t, "Restored after restart")
                elif t["status"] == "failed" and MISSING_APP_FILE.search(t.get("result") or ""):
                    t.update(status="queued", due=0, result="")
                    self.save(t, "Retried after the app's files moved")
                elif self.unpublished_issue_work(t):
                    # Earlier builds closed these as done without proposing a branch. The old
                    # clone's base is stale, so redo the work from the current default branch.
                    try:
                        worker.set_aside_worktree(self.db.root / "worktrees", t["id"])
                    except (OSError, worker.WorkerError) as exc:
                        self.db.log("Worktree kept", str(exc), t["project"], t["id"])
                        continue
                    t.update(status="queued", due=0, result="", worktree="", changes=[], partial_changes=False)
                    self.save(t, "Reopened: edits for this open issue were never proposed")
            for e in self.db.rows("events"):
                if e["state"] == "working":
                    e["state"] = "new"
                    self.db.put("events", e)
            self.apply_scope_to_tasks()
        self.write_markdown()
        self.legacy_questions = [
            (t["id"], t["version"]) for t in self.db.rows("tasks")
            if t["status"] == "question" and t.get("decision_id") and "answer_choices" not in t
            and not t.get("checkout_inaccessible")
        ]

    def unpublished_issue_work(self, t):
        """A coding task closed as done while its edits for a still-open GitHub issue went nowhere."""
        if (t["status"] != "done" or t.get("kind", "").lower() not in {"coding", "code"}
                or not t.get("changes") or t.get("milestones") or t.get("receipts")):
            return False
        e = self.db.get("events", t.get("event", ""))
        context = (e or {}).get("raw", {}).get("context", {})
        return context.get("is_pull_request") is False and context.get("state") == "open"

    def initialize_projects(self):
        projects = self.db.rows("projects")
        for project in projects:
            if "repo" in project:
                path = project.pop("repo")
                project.setdefault("repos", [path] if path else [])
                self.db.put("projects", project)
        for project in projects:
            if project.get("global"):
                return project["name"]
        # Keep existing rules and approvals scoped to any user-created namesake.
        name = "Global"
        number = 1
        while self.db.get("projects", name):
            name = "Global workspace" if number == 1 else f"Global workspace {number}"
            number += 1
        self.db.put("projects", {"id": name, "name": name, "global": True, "repos": []})
        return name

    @staticmethod
    def ancestors(name, parents):
        lineage = set()
        while (name := parents.get(name)) and name not in lineage:
            lineage.add(name)
        return lineage

    def add_project(self, name, repos, parent=None):
        previous = self.db.get("projects", name) or {"id": name, "name": name}
        if parent is not None:
            if not isinstance(parent, str) or not self.db.get("projects", parent):
                raise ValueError("Choose an existing parent project.")
            parents = {p["name"]: p.get("parent") for p in self.db.rows("projects")}
            if previous.get("global") or name == parent or name in self.ancestors(parent, parents):
                raise ValueError("Choose a parent outside this project's group.")
            previous["parent"] = parent
        if repos is None:
            repos = previous.get("repos", [])
        if isinstance(repos, str):  # Older clients replace their single visible repository.
            repos = ([repos] if repos else []) + previous.get("repos", [])[1:]
        if not isinstance(repos, list):
            raise ValueError("Choose a list of repository folders.")
        paths = []
        for repo in repos:
            if not isinstance(repo, str) or not repo.strip():
                raise ValueError("Choose a repository folder.")
            if repo in previous.get("repos", []) and self.foreign(previous, repo):
                paths.append(repo)  # Another instance's folder, kept as registered there.
                continue
            path = Path(repo).expanduser()
            if not path.is_absolute() or not (path / ".git").exists():
                raise ValueError(f"Choose a Git working tree for {repo}.")
            paths.append(str(path.resolve()))
        paths = list(dict.fromkeys(paths))
        origins = {k: v for k, v in (previous.get("origins") or {}).items() if k in paths}
        self.db.put("projects", {**previous, "repos": paths, "origins": origins})
        self.record_project_origins()
        if self.scope.active:
            self.refresh_scope()
        self.repository_recheck.set()
        self.wake.set()

    def resume_repository_questions(self):
        origins = {}
        for task in self.db.rows("tasks"):
            error = task.get("result", "")
            git_blocked = task["status"] == "failed" and not task.get("draft") and (
                error.startswith("Git cannot access ") and "folder permissions" in error
                or error == "Git could not prepare the isolated checkout. Check the repository and task branch."
            )
            command_blocked = task["status"] == "question" and re.search(
                r"\b(?:can[’']t|cannot|unable to) (?:run|execute) (?:the |shell |local )?(?:commands?|tests?|suites?)\b",
                " ".join((task.get("question", ""), task.get("question_context", ""), error)), re.I)
            if ((git_blocked or command_blocked) and not task.get("local_commands_available")
                    and not task.get("special") and not task.get("repository_question")
                    and self.rule(task["project"], task["kind"]) != "user"):
                with self.db.atomic():
                    if self.db.get("tasks", task["id"])["version"] != task["version"] or self.scope.task(task):
                        continue
                    try:
                        self.step_source(task)
                    except ValueError:
                        continue
                    task.update(status="queued", due=0, question="", decision_id="", local_commands_available=True)
                    self.save(task, "Retrying preparation in an app-owned workspace")
                    self.wake.set()
                continue
            legacy_context = (
                not task.get("context_checked") and not task.get("special") and not task.get("calendar_question")
                and self.rule(task["project"], task["kind"]) != "user"
                and ((task["status"] == "question" and re.search(
                    r"\b(worktree|checkout|parent task|file (?:contents|paths)|contents of [\w./-]+\.\w+)\b",
                    task.get("question", ""), re.I))
                    or (task["status"] == "failed" and task["result"] ==
                        "Git is unavailable or the isolated checkout operation timed out."))
            )
            if (task["status"] == "question" and task.get("repository_question")) or legacy_context:
                self.recheck_repository(task, origins)

    PULL_REFERENCE = re.compile(r"\b(?:PR|pull request)\s*(?:[\w.-]+/)?(?:[\w.-]+)?#(\d+)|/pull/(\d+)\b", re.I)

    def pull_request_number(self, task):
        """The pull request a task works on, so its copy starts at that PR's head. From the source event of the
        task or a step's ancestors, else from one PR named in the title, else in the body."""
        seen, current = set(), task
        while current and current["id"] not in seen:
            seen.add(current["id"])
            event = self.db.get("events", current.get("event", "")) if current.get("event") else None
            context = (event or {}).get("raw", {}).get("context", {})
            if context.get("is_pull_request") and str(context.get("number", "")).isdigit():
                return int(context["number"])
            current = self.step_parent(current)
        for text in (task.get("title", ""), task.get("body", "")):
            numbers = {int(a or b) for a, b in self.PULL_REFERENCE.findall(text)}
            if numbers:
                return numbers.pop() if len(numbers) == 1 else None
        return None

    def step_parent(self, task):
        if task.get("depth") and task.get("parent"):
            parent = self.db.get("tasks", task["parent"])
            if parent and parent["project"] == task["project"]:
                return parent

    def step_source(self, task):
        # Steps may use the originating issue to select a checkout. Its outbound
        # destination and completion criteria belong to the original task.
        while not task.get("event") and (parent := self.step_parent(task)):
            if parent.get("depth", 0) >= task["depth"]:
                break
            task = parent
        event = self.db.get("events", task.get("event", ""))
        conn = self.db.get("connections", event["connection"]) if event else None
        if task.get("event") and (not conn or conn.get("unavailable")):
            raise ValueError("The source account is disconnected.")
        return conn

    def recheck_repository(self, task, origins):
        if self.scope.task(task):
            return
        try:
            conn = self.step_source(task)
        except ValueError:
            return
        try:
            paths = self.repository_candidates(task, conn, origins)
            parent = self.step_parent(task) or {}
            selected = task.get("repository") or parent.get("repository")
            repository = selected if selected in paths else self.match_repository(task, conn, paths)
        except bugflow.CheckoutAccessError as exc:
            if task.get("checkout_inaccessible") and task["question"] == str(exc):
                return
            task.update(status="question", question=str(exc), repository_question=True, checkout_inaccessible=True,
                        reason="Waiting for access to the registered checkout")
        else:
            if not repository:
                if not task.get("repository_question"):
                    return
                choices = self.repository_answers(paths)
                prompt = "Which local checkout should this task use?" if paths else "Where is this task’s local checkout?"
                if task["question"] == prompt and task.get("repository_options") == paths and not task.get("checkout_inaccessible"):
                    return
                task.update(question=prompt, answer_choices=choices, checkout_inaccessible=False,
                            repository_options=paths, question_context="Choose a local Git checkout for this task.",
                            reason="Waiting for a checkout")
            else:
                task.update(status="queued", repository=repository, context_checked=True, repository_question=False,
                            checkout_inaccessible=False, question="",
                            decision_id="", due=0, reason="Found the matching checkout")
        with self.db.atomic():
            latest = self.db.get("tasks", task["id"])
            if latest["version"] != task["version"] or self.scope.task(latest):
                return
            if task["status"] == "queued" and self.scope.path(task["repository"]):
                return
            if task["status"] == "queued" and conn and conn["kind"] == "github" and self.can_group_task(task):
                project = self.checkout_project(conn, task["repository"])
                if not any(rule["project"] == project for rule in self.db.rows("rules")):
                    task["project"] = project
                event = self.db.get("events", task["event"])
                event["project"] = task["project"]
                self.db.put("events", event)
            self.save(task, task["reason"])
            self.wake.set()

    def can_group_task(self, task):
        return (task["project"] == self.global_project and task.get("event") and not task.get("parent")
                and not any(row.get("parent") == task["id"] for row in self.db.rows("tasks"))
                and not any(row["project"] == self.global_project for row in self.db.rows("rules")))

    def checkout_project(self, connection, path):
        """Called in the transaction assigning a task to a verified checkout."""
        projects = self.db.rows("projects")
        matches = [p["name"] for p in projects if not p.get("global") and path in self.project_paths(p)]
        parents = {p["name"]: p.get("parent") for p in projects}
        broader = {parent for name in matches for parent in self.ancestors(name, parents)}
        matches = [name for name in matches if name not in broader]
        if matches:
            return matches[0] if len(matches) == 1 else self.global_project
        slug = connection["config"]["repository"].casefold()
        if self.db.get("projects", slug):
            return self.global_project
        self.db.put("projects", dict(id=slug, name=slug, repos=[path], parent=self.global_project))
        self.repository_recheck.set()
        return slug

    def refresh_scope(self):
        self.scope = workscope.WorkScope(self.db)
        self.apply_scope_to_tasks()
        self.suggestions.next_scan = 0

    def apply_scope_to_tasks(self):
        for t in self.db.rows("tasks"):
            if t["status"] in TERMINAL | BUSY:
                continue
            if self.scope.task(t):
                if t["status"] != "paused":
                    self.save(t, "Paused by the workspace ignore list")
            elif t["status"] == "paused":
                t.update(status="scheduled" if t.get("due", 0) > self.now() else "queued",
                         reason="Removed from the ignore list")
                self.save(t, t["reason"])

    def repositories(self, project, clone=False):
        projects = self.db.rows("projects")
        parents = {p["name"]: p.get("parent") for p in projects}
        return list(
            dict.fromkeys(
                path
                for p in projects
                if project == self.global_project
                or p["name"] == project
                or project in self.ancestors(p["name"], parents)
                for path in self.project_paths(p, clone)
                if not self.scope.path(path)
            )
        )

    def foreign(self, project, path):
        """A repository path another instance registered. It names a folder on that machine, not this one."""
        info = (project.get("origins") or {}).get(path)
        return bool(info and info.get("instance") not in (None, self.instances.id))

    @staticmethod
    def project_origins(project):
        return {info.get("origin", "") for info in (project.get("origins") or {}).values()} - {""}

    def project_paths(self, project, clone=False):
        """This instance's folders for a project's repositories. A path registered on another instance maps to
        this instance's checkout of the same origin, cloned into the data directory when clone is set.
        See "Checkouts by origin" in instances-and-sync.md."""
        paths = []
        for path in project.get("repos", []):
            if not self.foreign(project, path):
                paths.append(path)
                continue
            info = project["origins"][path]
            local = self.find_checkout(info.get("origin", ""))
            if not local and clone and info.get("origin") and info.get("remote"):
                try:
                    local = self.checkout_for(info["origin"], info["remote"])
                except (ValueError, worker.WorkerError) as exc:
                    self.db.log("Repository not cloned", f"{info['origin']} for {project['name']}: {exc}")
            if local:
                paths.append(local)
        return paths

    def skill(self, kind, body, active, reason=""):
        body = str(body).strip()
        if not body:
            return
        if any(
            s["kind"] == kind and s["body"] == body and s["active"] == active for s in self.db.rows("skills")
        ):
            return
        if kind == "workflow" and active:
            for s in self.db.rows("skills"):
                if s["kind"] == kind and s["active"]:
                    s.update(active=False, status="superseded")
                    self.db.put("skills", s)
        s = dict(
            id=uid(),
            kind=kind,
            body=body,
            active=active,
            status="active" if active else "pending",
            at=self.now(),
            reason=reason,
        )
        self.db.put("skills", s)
        self.db.log("Skill applied" if active else "Style review", body + ("\n" + reason if reason else ""))

    def write_markdown(self):
        with self.db.lock:
            path = self.db.root / "skills"
            path.mkdir(exist_ok=True)
            for kind in ("workflow", "style"):
                texts = [s["body"] for s in self.db.rows("skills") if s["kind"] == kind and s["active"]]
                tmp = path / (kind + ".tmp")
                tmp.write_text("\n\n".join(texts), encoding="utf8")
                os.replace(tmp, path / (kind + ".md"))

    def settings(self):
        return self.db.get("config", "settings")

    def state(self):
        with self.db.lock:
            connections = self.db.rows("connections")
            inventories = {}
            for source in connections:
                if self.scope.connection(source):
                    source["status"] = "Ignored"
                if source.get("github_parent") and not source.get("unavailable"):
                    inventories.setdefault(source["github_parent"], []).append(
                        {"name": source["config"]["repository"], "status": source["status"]}
                    )
            for c in connections:
                if c.get("auth_method") == "gh_account":
                    c["repositories"] = inventories.get(c["id"], [])
            parents = {
                c["id"]: c.get("github_parent") or c.get("slack_parent") or c["id"] for c in connections
            }
            activity = self.db.history()
            for row in activity:
                row["connection"] = parents.get(row["connection"], row["connection"])
            tasks = self.db.dashboard_tasks()
            if self.instances.peers():
                for t in tasks:
                    if not self.owns("task:" + t["id"]):
                        t["owner"] = self.instances.owner_name("task:" + t["id"])
                for c in connections:
                    if not self.owns("connection:" + c["id"]):
                        c["owner"] = self.instances.owner_name("connection:" + c["id"])
            return dict(
                tasks=sorted(tasks, key=lambda t: t["created"], reverse=True),
                app_repository=selfcheck.REPOSITORY,
                activity=activity,
                connections=[
                    c for c in connections if not c.get("github_parent") and not c.get("slack_parent")
                ],
                rules=self.db.rows("rules"),
                settings=self.settings(),
                ignorelist=self.scope.config,
                **self.context.state(),
                skills=self.db.rows("skills")[::-1],
                suggestions=self.suggestions.state(),
                orchestrator=self.orchestrator.state(),
                instances=self.instances.state(),
                projects=sorted(self.db.rows("projects"), key=lambda p: not p.get("global")),
            )

    def new_task(self, title, project, body="", **fields):
        task = dict(
            id=uid(),
            title=title,
            project=project,
            body=body,
            status="queued",
            source="Local",
            kind="task",
            reason="",
            draft="",
            question="",
            result="",
            destination="",
            account="",
            version=0,
            created=self.now(),
            commitments=[],
            feedback=[],
            worktree="",
            receipts=[],
        )
        task.update(fields)
        if task["reason"]:
            task["surfaced_reason"] = task["reason"]
        self.save(task, "Task created")
        return task

    def save(self, t, message, connection=""):
        if t["status"] not in TERMINAL | BUSY and self.scope.task(t):
            t.update(status="paused", reason="Paused by the workspace ignore list", draft="", question="")
        if t["status"] != "question" or t.get("checkout_inaccessible"):
            t.pop("answer_choices", None)
            t.pop("question_context", None)
        t["version"] += 1
        self.db.put("tasks", t)
        self.db.log(t["status"], message, t["project"], t["id"], connection)

    def rule(self, project, kind):
        row = self.db.get("rules", digest(project, kind))
        return row["mode"] if row else "review"

    def require_current_task(self, task):
        latest = self.db.get("tasks", task["id"])
        if latest["version"] != task["version"] or self.scope.task(latest):
            raise ValueError("The task changed during preparation.")
        return latest

    def auto(self, t):
        kinds = {t["kind"], *t.get("categories", [])}
        if t["commitments"]:
            kinds.add("commitment")
        if t.get("takeover_version") == t["version"]:
            return False  # a version prepared by an instance that went silent is sent only after approval
        return not self.settings()["dry"] and all(self.rule(t["project"], k) == "auto" for k in kinds)

    def agent_directory(self, role, project, key, task):
        if task and task.get("worktree") and task["project"] == project:
            return worker.resume_worktree(task["worktree"], self.db.root / "worktrees", task["id"], task.get("repository"))

        root = self.db.root.resolve() / "agent-workspaces"
        folder = root / ("task-" + digest(key) if key else "role-" + role)
        for directory in (root, folder):
            if directory.is_symlink():
                raise ValueError("The app workspace directory must not be a symbolic link.")
            directory.mkdir(mode=0o700, exist_ok=True)
        return folder

    def infer(self, role, payload, project, task=""):
        with self.db.lock:
            initial = self.db.get("tasks", task) if task else None
            if project in self.scope.projects or (initial and (
                initial["status"] in {"closed", "paused"} or self.scope.task(initial)
            )):
                raise ValueError("This task is closed or ignored.")
            revisions = [s["id"] for s in self.db.rows("skills") if s["active"]]
            instructions = {
                kind: (self.db.root / "skills" / (kind + ".md")).read_text() for kind in ("style", "workflow")
            }
            rules = [r for r in self.db.rows("rules") if r["project"] == project]
        if role in {"task", "discovery", "decision"}:
            request = json.dumps(payload, ensure_ascii=False, default=str)[:4000]
            payload = {**payload, "context": self.context.for_agent(project, request, task)}
        provider = self.settings()["provider"]
        directory = self.agent_directory(role, project, task, initial)
        self.db.log("Agent started", f"{role} · {provider} · {directory} · skills {', '.join(revisions)}", project, task)
        task_data = payload.get("task")
        if isinstance(task_data, dict) and task_data.get("changes"):
            # The verified files selection supplies originals. Repeating full
            # saved edits here can exhaust the input budget on the next attempt.
            payload = dict(payload, task=dict(task_data, changes=[edit["path"] for edit in task_data["changes"]]))
        retries = initial.get("agent_retries", 0) if initial and initial["status"] == "working" else 0
        deadline = {"timeout": worker.AGENT_TIMEOUT * (1 + min(retries, 2))} if retries else {}
        request = {**payload, "instructions": instructions, "project": project, "rules": rules}
        # Only ordinary preparation can request a command. Authority comes from
        # the current task record, never from source content or a CLI response.
        can_run = role == "task" and initial and initial["status"] == "working" and "available_paths" in payload
        results = []
        setups = 0
        tools = self.db.root / "toolchains"
        cache = self.db.root / "toolcache" / ("task-" + task) if task else None
        # Task agents get an app MCP session scoped to their task, revoked when the agent returns.
        token = None
        if role == "task" and task and self.mcp_url:
            token = self.mcp_sessions.issue(app_mcp.TASK, task, self.task_actions(task))
        mcp = dict(url=self.mcp_url, token=token) if token else None
        try:
            while True:
                commands = sum(1 for r in results if not r.get("setup"))
                if can_run:
                    request = dict(request, local_commands_remaining=4 - commands, setup_steps_remaining=4 - setups,
                                   command_results=list(results))
                answer = worker.run_agent(role, request, provider, directory, **deadline, **({"mcp": mcp} if mcp else {}))
                if initial:
                    self.require_current_task(initial)
                if answer.get("outcome") not in ("run", "setup"):
                    self.db.log("Agent result", json.dumps(answer, ensure_ascii=False), project, task)
                    return answer
                if answer.get("outcome") == "setup":
                    if not can_run or setups >= 4:
                        raise worker.WorkerError("No further setup steps are available for this request.")
                    setups += 1
                    result = self.setup_step(directory, answer["text"], tools, cache)
                    self.db.log("Setup step", json.dumps(result, ensure_ascii=False), project, task)
                    self.require_current_task(initial)
                    results.append(result)
                    continue
                if not can_run or commands >= 4:
                    raise worker.WorkerError("No further local commands are available for this request.")
                command = answer["text"]
                result = dict(command=command, **worker.run_command(directory, command, tools=tools, cache=cache))
                self.db.log("Local command", json.dumps(result, ensure_ascii=False), project, task)
                self.require_current_task(initial)
                results.append(result)
        finally:
            if token:
                self.mcp_sessions.revoke(token)

    def setup_step(self, directory, text, tools, cache):
        """Install an official toolchain or fetch dependencies for a task agent, without asking the user.
        See "Toolchains and dependencies" in local-agent.md."""
        base = dict(command=text, setup=True)
        try:
            action, kind, folder = toolchains.parse(text, directory)
            if action == "install":
                with self.toolchain_lock:
                    detail = toolchains.install(tools, kind)
                return dict(base, exit_code=0, timed_out=False, truncated=False, output=detail)
            result = worker.run_command(directory, toolchains.fetch_command(kind, folder), tools=tools,
                                        cache=cache, network=True, cwd=folder, timeout=900)
            return dict(base, **result)
        except (toolchains.SetupError, worker.WorkerError, OSError, ValueError, KeyError, TypeError,
                tarfile.TarError, subprocess.SubprocessError) as exc:
            return dict(base, exit_code=1, timed_out=False, truncated=False, output=f"{type(exc).__name__}: {exc}")

    def task_actions(self, key):
        """Action tools a task agent may call: its declared type, or the kind of its source."""
        t = self.db.get("tasks", key) or {}
        declared = app_mcp.TASK_TYPES.get(t.get("task_type", ""))
        if declared:
            return declared["actions"]
        e = self.db.get("events", t.get("event", ""))
        c = self.db.get("connections", e["connection"]) if e else None
        if not c or t.get("kind") in {"bug", "bugfix", "coding", "code"} or t.get("special"):
            return ()
        source = e["raw"].get("context", {}).get("google_service") or c["kind"]
        return tuple(a for a, kinds in app_mcp.ACTION_SOURCES.items() if c["kind"] in kinds or source in kinds)

    def start(self):
        self.thread = threading.Thread(target=self.loop, daemon=True, name="scheduler")
        self.thread.start()
        self.instances.on_change = self.instances.wake.set
        self.instances.start(capacity=self.capacity, idle=self.idle, changed=self.wake.set)

    def owns(self, item):
        return self.instances.owns(item)

    def idle(self, item):
        """An item can be handed over only while nothing runs or sends for it."""
        kind, _, key = item.partition(":")
        if kind == "task":
            t = self.db.get("tasks", key)
            return not ({"task:" + key, "send:" + key} & set(self.jobs)) and (
                not t or t["status"] not in {"working", "sending"})
        if kind == "connection":
            return "sync:" + key not in self.jobs
        return "orchestrator" not in self.jobs

    def slots(self):
        configured = self.settings().get("slots")
        if configured:
            return int(configured)
        return 2 if os.uname().sysname == "Darwin" else max(1, (os.cpu_count() or 2) - 1)

    def running_tasks(self):
        return sum(1 for key, job in self.jobs.items() if key.startswith("task:") and not job.done())

    def agent_states(self):
        try:
            return {row["id"]: row["state"] for row in self.agents.inventory()["harnesses"]}
        except Exception:
            return {}

    def on_battery(self):
        """Mac only, checked at most once a minute."""
        if os.uname().sysname != "Darwin":
            return False
        cached = getattr(self, "_battery", None)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=5).stdout
            value = "Battery Power" in out
        except Exception:
            value = False
        self._battery = (time.monotonic(), value)
        return value

    def absent(self):
        """Preparing for sleep or stop. Expires on its own, so waking from sleep resumes work."""
        return self.absence and self.absence[1] > self.now()

    def capacity(self):
        """Advertised to peers on every exchange. See "Phase 3 design" in instances-and-sync.md."""
        settings = self.settings()
        slots = self.slots()
        states = self.agent_states()
        return dict(
            slots=slots, free=max(0, slots - self.running_tasks()),
            preference=settings.get("preference", "first"),
            state=self.absence[0] if self.absent() else "active",
            battery=self.on_battery(),
            task_ready=states.get(settings.get("provider", "")) == "ready",
            hermes_ready=states.get("hermes") == "ready",
            origins=self.local_origins(),
            can_clone=bool(shutil.which("git")),
        )

    @staticmethod
    def eligible(info):
        return bool(info) and info.get("preference") != "never" and info.get("task_ready") \
            and info.get("state", "active") == "active"

    @staticmethod
    def rank(peer):
        info = peer["capacity"]
        return (info.get("preference") != "first", bool(info.get("battery")), -info.get("free", 0), peer["id"])

    def fresh_peers(self):
        now = self.now()
        found = []
        for p in self.instances.peers():
            info = (p.get("info") or {}).get("capacity")
            if p.get("trusted") and info and now - p.get("last_seen", 0) <= instances.FRESH_SECONDS:
                found.append(dict(id=p["id"], name=p["name"], capacity=dict(info),
                                  platform=(p.get("info") or {}).get("platform", "")))
        return found

    def pinned(self, t):
        """Tasks that stay with their owner: local evidence, desktop sources, app checks, and code work whose
        repository is not chosen yet. Code work with a repository moves to instances with that origin."""
        if any("connection" not in e for e in t.get("evidence") or []):
            return True
        if t.get("special") in {"self_check", "app_finding"}:
            return True
        if t.get("kind") in {"bug", "bugfix", "coding", "code"} and not t.get("repository"):
            return True
        e = self.db.get("events", t.get("event", "")) if t.get("event") else None
        c = self.db.get("connections", e["connection"]) if e else None
        return bool(c and c.get("workspace_profile"))

    # Checkouts by origin, for worktree handoff. See "Worktree handoff" in instances-and-sync.md.

    def checkout_paths(self):
        local = self.db.get("local", "checkouts") or {}
        paths = list(local.get("paths", []))
        for p in self.db.rows("projects"):
            paths += [path for path in p.get("repos", []) if not self.foreign(p, path)]
        return [path for path in dict.fromkeys(paths) if os.path.isdir(path)]

    def origin_of(self, path):
        cache = self.origin_cache
        if path not in cache or time.monotonic() - cache[path][0] > 60:
            cache[path] = (time.monotonic(), worker.checkout_origin(path))
        return cache[path][1]

    def local_origins(self):
        return sorted({o for o in (self.origin_of(p) for p in self.checkout_paths()) if o})

    def find_checkout(self, origin):
        return next((p for p in self.checkout_paths() if self.origin_of(p) == origin), None)

    def required_origin(self, t):
        if not t.get("repository"):
            return ""
        if t.get("repository_origin"):
            return t["repository_origin"]
        return self.origin_of(t["repository"]) if self.owns("task:" + t["id"]) else ""

    def can_take(self, t, info):
        origin = self.required_origin(t)
        if t.get("repository") and not origin:
            return False
        return not origin or origin in (info.get("origins") or []) or bool(info.get("can_clone"))

    def checkout_for(self, origin, remote):
        """This instance's checkout of an origin, cloned into the data directory when missing."""
        path = self.find_checkout(origin)
        if path:
            return path
        if not remote:
            raise ValueError(f"No checkout of {origin} here and no address to clone it from.")
        with self.clone_lock:
            return self.clone_for(origin, remote)

    def share_github_logins(self):
        """Copy each GitHub CLI login that exists here into the synced store, so peers can read the same
        repositories. Checked every ten minutes. See "Logins" in instances-and-sync.md."""
        if self.now() < self.next_github_share:
            return
        self.next_github_share = self.now() + 600
        for c in self.db.rows("connections"):
            if c.get("auth_method") != "gh_account" or not c.get("identity"):
                continue
            try:
                token = github_signin.token_for(c["identity"])
            except (RuntimeError, OSError):
                continue  # No GitHub CLI login for this account on this instance.
            key = GITHUB_CLI_LOGIN + c["identity"].casefold()
            try:
                current = connectors._secret(key)
            except Exception:
                current = None
            if current != token:
                SyncedSecrets.store(key, token)
                self.instances.record_secret(key, token)

    def github_token(self, origin):
        """A synced GitHub login for github.com/owner/repo: the account that connected that repository, else
        the only connected account."""
        if not origin or not origin.startswith("github.com/"):
            return None
        slug = origin.split("/", 1)[1].casefold()
        connections = self.db.rows("connections")
        accounts = {c["id"]: c for c in connections if c.get("auth_method") == "gh_account" and c.get("identity")}
        owner = next((accounts.get(c.get("github_parent")) for c in connections
                      if c.get("kind") == "github" and c.get("config", {}).get("repository", "").casefold() == slug
                      and c.get("github_parent") in accounts), None)
        if not owner and len(accounts) == 1:
            owner = next(iter(accounts.values()))
        if not owner:
            return None
        try:
            return connectors._secret(GITHUB_CLI_LOGIN + owner["identity"].casefold()) or None
        except Exception:
            return None

    def clone_for(self, origin, remote):
        self.origin_cache.clear()
        path = self.find_checkout(origin)
        if path:
            return path
        target = self.db.root / "checkouts" / re.sub(r"[^\w.-]+", "_", origin).strip("_")
        token = self.github_token(origin)
        if token and origin.startswith("github.com/"):
            remote = "https://" + origin  # The synced login works over HTTPS, whatever protocol the Mac uses.
        path = str(worker.clone_checkout(remote, target, token=token))
        if worker.checkout_origin(path) != origin:
            raise ValueError(f"The clone of {remote} has a different origin.")
        with self.db.atomic():
            local = self.db.get("local", "checkouts") or {"id": "checkouts", "paths": []}
            local["paths"] = list(dict.fromkeys(local["paths"] + [path]))
            self.db.put("local", local)
        self.origin_cache.clear()
        self.db.log("Repository cloned", f"{origin} into {path} for a task handed to this instance")
        return path

    def package(self, item, to):
        """Runs before an offer: a task's repository origin, and its worktree as a bundle."""
        kind, _, key = item.partition(":")
        t = self.db.get("tasks", key) if kind == "task" else None
        if not t or not t.get("repository"):
            return None
        origin = self.origin_of(t["repository"])
        if not origin:
            raise ValueError("The task's checkout has no origin remote, so no other instance can take it.")
        package = dict(origin=origin, remote=worker.checkout_remote(t["repository"]), task=key)
        if t.get("worktree"):
            package.update(worker.export_worktree(t["worktree"], key))
        return package

    def accept(self, offer, package):
        """Runs before confirming an offer: recreate a task's worktree in this instance's checkout."""
        kind, _, key = offer["item"].partition(":")
        if kind != "task" or not package:
            return {}
        path = self.checkout_for(package["origin"], package.get("remote", ""))
        fields = dict(repository=path, repository_origin=package["origin"],
                      repository_remote=package.get("remote", ""))
        if package.get("bundle"):
            fields["worktree"] = str(worker.import_worktree(path, self.db.root / "worktrees", key, package,
                                                            token=self.github_token(package["origin"])))
        self.db.log("Task received", f"{key} with {'its worktree' if package.get('bundle') else 'its repository'}"
                    f" in {path}", task=key)
        return fields

    def takeover_rank(self, instance_id, info, platform):
        return (info.get("state", "active") != "active", platform == "darwin", bool(info.get("battery")), instance_id)

    def auto_takeover(self):
        """Take over from instances silent for TAKEOVER_SECONDS, or removed, without asking the user.
        Only the best-ranked live instance does it. See "Automatic takeover" in instances-and-sync.md."""
        now = self.now()
        gone = [p for p in self.db.rows("peers") if p.get("removed") or (
            p.get("trusted") and now - p.get("last_seen", p.get("paired_at", now)) > TAKEOVER_SECONDS)]
        if not gone:
            return 0
        me = self.capacity()
        mine = self.takeover_rank(self.instances.id, me, self.instances.me()["platform"])
        if any(self.takeover_rank(p["id"], p["capacity"], p["platform"]) < mine for p in self.fresh_peers()):
            return 0
        taken = 0
        for peer in gone:
            items = [r["id"] for r in self.db.rows("owners") if r["owner"] == peer["id"] and self.can_serve(r["id"], me)]
            if items:
                taken += self.take_items(peer, items)
        return taken

    def can_serve(self, item, me):
        kind, _, key = item.partition(":")
        if kind == "connection":
            c = self.db.get("connections", key)
            return bool(c and self.movable_connection(c))
        if kind == "task":
            t = self.db.get("tasks", key)
            if not t or t["status"] in TERMINAL or self.pinned(t):
                return False
            return not t.get("repository") or bool(t.get("repository_origin") and (
                self.find_checkout(t["repository_origin"]) or t.get("repository_remote")))
        return item == instances.ORCHESTRATOR and me.get("hermes_ready")

    def take_items(self, peer, items):
        # Prepare repository checkouts first, outside the database transaction.
        checkouts = {}
        for item in items:
            t = self.db.get("tasks", item[5:]) if item.startswith("task:") else None
            if t and t.get("repository"):
                try:
                    checkouts[item] = self.checkout_for(t["repository_origin"], t.get("repository_remote", ""))
                except Exception as exc:
                    self.db.log("Not taken over", f"{item}: {exc}")
        items = [i for i in items if not i.startswith("task:") or i in checkouts
                 or not (self.db.get("tasks", i[5:]) or {}).get("repository")]
        taken = self.instances.takeover(peer["id"], items)
        with self.db.atomic():
            for item in taken:
                kind, _, key = item.partition(":")
                t = self.db.get("tasks", key) if kind == "task" else None
                if not t:
                    continue
                note = f"Taken over from {peer['name']}, which went silent"
                if item in checkouts:
                    t["repository"] = checkouts[item]
                if t.get("worktree"):
                    t.update(worktree="", status="queued", draft="",
                             reason=f"Restarted here: the worktree on {peer['name']} could not be reached")
                elif t["status"] == "sending":
                    t.update(status="uncertain", result=note + " during a send. Check whether it was delivered "
                             "before sending again.")
                elif t["status"] == "working":
                    t["status"] = "queued"
                if t["status"] == "review":
                    t["takeover_version"] = t["version"] + 1  # save() moves the version to this value
                self.save(t, note)
            self.db.log("Taken over", f"{len(taken)} items from {peer['name']}, silent or removed")
        self.wake.set()
        return len(taken)

    def record_project_origins(self):
        """Note the origin of each project folder that exists here, so peers can map it to their own checkout."""
        for p in self.db.rows("projects"):
            known = p.get("origins") or {}
            found = {}
            for path in p.get("repos", []):
                if path not in known and os.path.isdir(path) and (origin := self.origin_of(path)):
                    found[path] = dict(origin=origin, remote=worker.checkout_remote(path), instance=self.instances.id)
            if found:
                with self.db.atomic():
                    latest = self.db.get("projects", p["id"])
                    if latest:
                        merged = {**found, **(latest.get("origins") or {})}
                        self.db.put("projects", {**latest, "origins": {k: v for k, v in merged.items()
                                                                       if k in latest.get("repos", [])}})

    def record_origins(self):
        """Note each owned repository task's origin, so a peer can take it over if this instance goes silent."""
        self.record_project_origins()
        for t in self.db.rows("tasks"):
            if t.get("repository") and not t.get("repository_origin") and t["status"] not in TERMINAL \
                    and self.owns("task:" + t["id"]):
                origin = self.origin_of(t["repository"])
                if origin:
                    with self.db.atomic():
                        self.db.put("tasks", {**self.db.get("tasks", t["id"]), "repository_origin": origin,
                                              "repository_remote": worker.checkout_remote(t["repository"])})

    def prefer_always_on(self, peers):
        """On a Mac, offer connections and the orchestrator to an active non-Mac peer."""
        if self.instances.me()["platform"] != "darwin" or self.absent():
            return
        servers = [p for p in peers if p["platform"] != "darwin" and p["capacity"].get("state", "active") == "active"]
        if not servers:
            return
        target = min(servers, key=lambda p: p["id"])
        now = self.now()
        items = ["connection:" + c["id"] for c in self.db.rows("connections") if self.movable_connection(c)]
        if target["capacity"].get("hermes_ready"):
            items.append(instances.ORCHESTRATOR)
        for item in items:
            if self.owns(item) and item not in self.recent_offers and self.instances.offer(item, target["id"], self.idle):
                self.recent_offers[item] = now

    @staticmethod
    def movable_connection(c):
        """Connections a peer can poll with synced logins."""
        return not (c.get("workspace_profile") or c.get("github_parent")
                    or c.get("auth_method") in {"gh_account", "gh_repository"})

    def balance(self):
        """Offer queued work to peers with capacity, and ask for work when idle."""
        peers = self.fresh_peers()
        if not peers:
            return
        self.share_github_logins()
        now = self.now()
        me = self.capacity()
        free = me["free"] if self.eligible(me) else 0
        first = me["preference"] == "first"
        offered = self.recent_offers
        for key in [k for k, at in offered.items() if now - at > 60]:
            del offered[key]
        mine, theirs = [], []
        for t in sorted(self.db.rows("tasks"), key=lambda t: t.get("created", 0)):
            if t["status"] not in {"queued", "scheduled"} or t.get("due", 0) > now or self.pinned(t):
                continue
            item = "task:" + t["id"]
            if self.owns(item):
                if "task:" + t["id"] not in self.jobs:
                    mine.append((item, t))
            elif self.can_take(t, me):
                theirs.append(item)
        self.prefer_always_on(peers)
        self.record_origins()
        for item, t in mine:
            t = self.db.get("tasks", t["id"]) or t
            ranked = sorted((p for p in peers if self.eligible(p["capacity"]) and p["capacity"].get("free", 0) > 0
                             and self.can_take(t, p["capacity"])
                             and not self.instances.declined_recently(item, p["id"])), key=self.rank)
            best = ranked[0] if ranked else None
            stay = free > 0 and not (not first and best and best["capacity"].get("preference") == "first")
            if stay:
                free -= 1
            elif best and item not in offered:
                if self.instances.offer(item, best["id"], self.idle):
                    offered[item] = now
                    best["capacity"]["free"] -= 1
                    self.db.log("Handing over", f"{item} to {best['name']}: "
                                + ("no free slot here" if self.eligible(me) else "not eligible here"))
        if free <= 0 or self.absent():
            return
        owners = {p["id"]: p for p in peers}
        for item in theirs:
            record = self.instances.owner(item)
            owner = owners.get(record["owner"]) if record else None
            if not owner or item in offered or free <= 0:
                continue
            info = owner["capacity"]
            if info.get("free", 0) <= 0 or not self.eligible(info):
                self.instances.request_move(item, self.instances.id)
                offered[item] = now
                free -= 1

    def prepare_absence(self, reason="sleeping", timeout=20, exchange=None):
        """Hand idle items to the best fresh peer before sleep or stop. Returns (handed, kept)."""
        deadline = self.now() + timeout
        self.absence = (reason, deadline + 60)
        exchange = exchange or self.instances.exchange_all
        # Background sync keeps peer freshness current; a peer silent for a minute is not worth a wait.
        if self.fresh_peers():
            exchange(self.capacity)
        peers = sorted(self.fresh_peers(), key=self.rank)
        if not peers:
            self.db.log("Absence", f"{reason.title()} with no reachable instance; work stays here.")
            return 0, 0
        task_peers = [p for p in peers if p["capacity"].get("preference") != "never"
                      and p["capacity"].get("task_ready")] or []
        hermes_peers = [p for p in peers if p["capacity"].get("hermes_ready")]
        wanted = {}
        for t in self.db.rows("tasks"):
            if t["status"] in TERMINAL or self.pinned(t) or not self.owns("task:" + t["id"]):
                continue
            target = next((p for p in task_peers if self.can_take(t, p["capacity"])), None)
            if target:
                wanted["task:" + t["id"]] = target
        for c in self.db.rows("connections"):
            if self.movable_connection(c) and self.owns("connection:" + c["id"]):
                wanted["connection:" + c["id"]] = peers[0]
        if hermes_peers and self.owns(instances.ORCHESTRATOR):
            wanted[instances.ORCHESTRATOR] = hermes_peers[0]
        offered = set()
        while True:
            for item, peer in wanted.items():
                if item not in offered and self.instances.offer(item, peer["id"], self.idle):
                    offered.add(item)
            self.instances.wake.set()
            exchange(self.capacity)
            handed = [i for i in offered if (self.instances.owner(i) or {}).get("owner") != self.instances.id]
            if len(handed) == len(wanted) or self.now() >= deadline:
                break
            time.sleep(0.3)
        kept = len(wanted) - len(handed)
        self.db.log("Absence", f"{reason.title()}: handed {len(handed)} items to {peers[0]['name']}"
                    + (f"; {kept} stayed here" if kept else ""))
        return len(handed), kept

    def close(self):
        if connectors.secret_listener == self.instances.record_secret:
            connectors.secret_listener = None
        self.instances.close()
        self.orchestrator.close()
        self.signins.close()
        self.stop.set()
        self.wake.set()
        if hasattr(self, "thread"):
            self.thread.join(2)
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.sync_pool.shutdown(wait=True, cancel_futures=True)
        self.db.conn.close()

    def loop(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.db.log("Scheduler error", str(exc))
            self.wake.wait(1)
            self.wake.clear()

    @staticmethod
    def related_evidence(task, parent, children, siblings):
        def completed(rows):
            return [dict(id=r["id"], title=r["title"], result=r["result"][:3000])
                    for r in rows if r["id"] != task["id"] and r.get("depth") and r["status"] == "done"]

        return dict(children=completed(children), siblings=completed(siblings),
                    parent_answers=[{k: str(v)[:1500] for k, v in entry.items()}
                                    for entry in parent["feedback"][-3:]] if parent else [],
                    parent_result=parent["result"][:3000] if parent and parent["status"] == "done" else "")

    def tick(self):
        for key, job in list(self.jobs.items()):
            if job.done():
                del self.jobs[key]
                try:
                    job.result()
                except Exception as exc:
                    self.db.log("Job failed", str(exc))
        now = self.now()

        def submit(key, fn, arg, pool):
            if key not in self.jobs:
                self.jobs[key] = pool.submit(fn, arg)

        # Only the owner of an item works on it: see the ownership protocol in instances-and-sync.md.
        purpose = self.orchestrator.due(now) if self.owns(instances.ORCHESTRATOR) else None
        if purpose:
            submit("orchestrator", self.orchestrator.run, purpose, self.sync_pool)
        events = self.db.rows("events")
        self.close_finished_sources(events)
        tasks = self.db.rows("tasks")
        probe_due = now >= self.next_repository_probe and any(
            t["status"] == "question" and (t.get("checkout_inaccessible") or t.get("repository_question")) for t in tasks)
        if (self.repository_recheck.is_set() or probe_due) and "repository-checks" not in self.jobs:
            self.repository_recheck.clear()
            self.next_repository_probe = now + 60
            self.jobs["repository-checks"] = self.pool.submit(self.resume_repository_questions)
        self.context.due()
        if self.suggestions.next_scan <= now:
            submit("activity-suggestions", self.suggestions.run, None, self.pool)
        owned = set()
        for c in self.db.rows("connections"):
            if not self.owns("connection:" + c["id"]):
                continue
            owned.add(c["id"])
            if not c.get("unavailable") and c.get("next", 0) <= now and not self.scope.connection(c):
                submit("sync:" + c["id"], self.sync, c, self.sync_pool)
        for request in self.db.rows("dispatch_requests"):
            if not request.get("done") and request.get("connection") in owned:
                submit("dispatch:" + request["id"], self.mcp_tools.fulfil, request["id"], self.pool)
        if now >= self.next_balance:
            self.next_balance = now + 5
            self.balance()
            if self.instances.peers() or any(p.get("removed") for p in self.db.rows("peers")):
                submit("takeover", lambda _: self.auto_takeover(), None, self.sync_pool)
        running, slots, absent = self.running_tasks(), self.slots(), self.absent()
        by_id = {t["id"]: t for t in tasks}
        steps = {}
        for row in tasks:
            if row.get("depth"):
                steps.setdefault((row["project"], row["parent"]), []).append(row)
        for t in sorted(tasks, key=lambda t: t.get("created", 0)):
            # Only the owner of an item works on it: see the ownership protocol in instances-and-sync.md.
            if not self.owns("task:" + t["id"]):
                continue
            if t["status"] == "question" and t.get("related_snapshot"):
                parent = by_id.get(t.get("parent")) if t.get("depth") else None
                siblings = steps.get((t["project"], t.get("parent")), []) if parent else []
                evidence = self.related_evidence(t, parent, steps.get((t["project"], t["id"]), []), siblings)
                if digest(evidence) != t["related_snapshot"]:
                    with self.db.atomic():
                        if self.db.get("tasks", t["id"])["version"] != t["version"]:
                            continue
                        t.update(status="queued", due=0, decision_id="", related_snapshot="")
                        self.save(t, "New evidence from a related task")
            if t["status"] in {"queued", "scheduled"} and t.get("due", 0) <= now:
                # At most the slot count at once; nothing new while preparing for sleep or stop.
                if "task:" + t["id"] not in self.jobs and not absent and running < slots:
                    submit("task:" + t["id"], self.process, t["id"], self.pool)
                    running += 1
            if t["status"] == "review" and t.get("approved_version") == t["version"]:
                submit("send:" + t["id"], self.approved_send, (t["id"], t["version"]), self.sync_pool)
            elif t["status"] == "review" and self.auto(t):
                submit("send:" + t["id"], self.autosend, (t["id"], t["version"]), self.sync_pool)
        for e in events:
            if (e.get("connection") and e["connection"] not in owned) or self.scope.matches(e):
                continue
            if e["state"] == "new" or (e["state"] == "ignored" and e.get("next", 0) <= now):
                submit("discover:" + e["id"], self.discover, e["id"], self.pool)
        if self.legacy_questions and "format-question" not in self.jobs:
            submit("format-question", self.format_existing_question, self.legacy_questions.pop(), self.pool)

    def close_finished_sources(self, events=None):
        """Close open tasks whose GitHub issue or PR was closed after they began. See task-workflow.md."""
        latest = {}
        for e in self.db.rows("events") if events is None else events:
            raw = e.get("raw") or {}
            context = raw.get("context") or {}
            if "is_pull_request" not in context or not raw.get("destination"):
                continue
            if raw["destination"] not in latest or raw["timestamp"] >= latest[raw["destination"]]["timestamp"]:
                latest[raw["destination"]] = raw
        finished = {d: r for d, r in latest.items() if (r.get("context") or {}).get("state") == "closed"}
        if not finished:
            return
        tasks = self.db.rows("tasks")
        by_id = {t["id"]: t for t in tasks}

        def destination(t):
            # A step without its own destination works on its parent's item.
            seen = set()
            while not t.get("destination") and t.get("depth") and t.get("parent") not in seen:
                seen.add(t.get("parent"))
                t = by_id.get(t.get("parent")) or {}
            return t.get("destination")

        for t in tasks:
            raw = finished.get(destination(t))
            if not raw or t["status"] in TERMINAL | BUSY | {"working"} or not self.owns("task:" + t["id"]):
                continue
            context = raw["context"]
            if (context.get("closed_at") or raw["timestamp"]) < t.get("created", 0):
                continue  # the task was made for an item that was already closed
            with self.db.atomic():
                current = self.db.get("tasks", t["id"])
                if not current or current["version"] != t["version"]:
                    continue
                how = "merged" if context.get("merged") else "closed"
                kind = "PR" if context.get("is_pull_request") else "issue"
                current.update(status="closed", draft="", question="", decision_id="",
                               reason=f"The {kind} was {how} on GitHub, so nothing is left to do here.")
                self.save(current, f"Closed because {raw['destination']} was {how} on GitHub")

    def ingest(self, conn, raw):
        with self.db.atomic():
            if self.scope.connection(conn) or self.scope.matches(raw):
                return
            current = self.db.get("connections", conn["id"])
            if not current or current.get("unavailable") or current.get("revision") != conn.get("revision"):
                return
            key = digest(conn["id"], raw["key"])
            prev = self.db.get("events", key)
            if self.scope.matches(prev):
                return
            if prev and prev["raw"] == raw:
                return
            e = dict(
                id=key,
                connection=conn["id"],
                project=prev["project"] if prev else "",
                raw=raw,
                state="new",
                generation=(prev or {}).get("generation", 0) + 1,
            )
            t = self.db.get("tasks", key)
            if t and t["status"] not in TERMINAL | BUSY:
                t.update(
                    status="skipped" if raw.get("owned") else "queued",
                    body=raw["body"],
                    draft="",
                    question="",
                )
                self.save(t, "Source updated; previous draft retired")
            self.db.put("events", e)
            if not raw.get("authored"):
                for related in self.db.rows("events"):
                    if related["id"] == key or not self.same_thread(e, related):
                        continue
                    other = self.db.get("tasks", related["id"])
                    if (
                        other
                        and other["status"] in {"working", "review", "queued"}
                        and not other.get("milestones")
                    ):
                        other.update(status="queued", draft="", reason="New activity in the source thread")
                        self.save(other, "Rechecking ownership and remaining work")
            self.db.log(
                "Activity read",
                f"{conn['label']} · {raw['title']}\n{raw.get('url', '')}\n{raw['body']}",
                e["project"],
                key,
                conn["id"],
            )

    def sync(self, conn, window=None):
        if self.scope.connection(conn):
            return
        end = self.now()
        start = conn.get("cursor", end - 10800)
        start, end = window or (start, min(end, start + 10800))
        try:
            if conn.get("auth_method") == "slack_workspace":
                self.signins.refresh_workspace(conn)
                return
            if conn.get("auth_method") == "gh_account":
                self.sync_github_inventory(conn)
                return
            if conn.get("google_services"):
                self.sync_google(conn, window)
                return
            if conn["kind"] == "slack":
                threads = {
                    e["raw"].get("context", {}).get("thread")
                    for e in self.db.rows("events")
                    if e["connection"] == conn["id"]
                }
                conn = {**conn, "known_threads": sorted(t for t in threads if t)}
            for raw in connectors.poll(conn, start, end):
                if start <= raw["timestamp"] <= end:
                    self.ingest(conn, raw)
            if not window:
                self.connection_result(conn, "Connected", cursor=end)
            self.db.log(
                "Sync complete", f"{conn['label']} · {int(start)} – {int(end)}", connection=conn["id"]
            )
        except Exception as exc:
            self.connection_result(conn, "Needs attention: " + str(exc))
            if window:
                raise

    def sync_google(self, conn, window):
        now = self.now()
        errors = []
        for service in conn["google_services"]:
            key = digest(conn["id"], service)
            cursor = self.db.get("google_cursors", key) or {}
            start, end = window or (max(now - 10800, cursor.get("at", 0)), now)
            try:
                if conn.get("workspace_profile"):
                    events = workspace_bridge.poll(self.db.root, conn, service, start, end)
                elif service == "Gmail":
                    events = connectors.poll(conn, start, end)
                else:
                    token = connectors._transport_secret(conn, connectors.validate("email", conn["config"]))
                    events = google_workspace.activity(service, conn["identity"], token, start, end)
                for raw in events:
                    if start <= raw["timestamp"] <= end:
                        self.ingest(conn, raw)
                self.db.log("Sync complete", service, connection=conn["id"])
                if not window:
                    with self.db.atomic():
                        current = self.db.get("connections", conn["id"])
                        if current and current["revision"] == conn["revision"]:
                            self.db.put("google_cursors", dict(id=key, at=max(end, cursor.get("at", 0))))
            except Exception as exc:
                errors.append(f"{service}: {exc}")
        if errors:
            raise ValueError(" · ".join(errors))
        if not window:
            self.connection_result(conn, "Connected", cursor=now)

    def calendars(self):
        return {
            c["identity"]: c
            for c in self.db.rows("connections")
            if "Calendar" in c.get("google_services", [])
        }

    def calendar_preview(self, task, event, details):
        calendars = self.calendars()
        origin = self.db.get("connections", event["connection"]) if event else {}
        address = (origin or {}).get("identity")
        if address not in calendars:
            address = None
        if task.get("calendar_choice") in calendars:
            address = task["calendar_choice"]
        if not address and len(calendars) == 1:
            address = next(iter(calendars))
        if not address:
            prompt = (
                ("Which Google calendar account should receive this event? " + ", ".join(calendars))
                if calendars
                else "Connect a Google account with Calendar access, then retry this event."
            )
            decision = self.decision(task, prompt)
            return {
                **decision,
                "status": "question",
                "question": decision.get("question") or prompt,
                "calendar_details": details,
                "calendar_question": True,
                "answer_choices": self.calendar_answers(calendars),
            }
        body = google_workspace.calendar_payload(
            task["id"], details["title"], details["start"], details["end"]
        )
        if calendars[address].get("workspace_profile"):
            body = workspace_bridge.calendar_payload(body)
        return dict(
            status="review",
            operation="calendar_event",
            kind="calendar",
            google=calendars[address]["id"],
            account=address,
            destination="Primary calendar",
            draft=json.dumps(body, ensure_ascii=False, indent=2),
            calendar_payload=body,
            calendar_question=False,
            calendar_details=details,
            reason="Review the deadline event.",
            complete_after_send=True,
        )

    def connection_result(self, conn, status, cursor=None):
        with self.db.atomic():
            c = self.db.get("connections", conn["id"])
            if not c or c.get("revision") != conn.get("revision"):
                return
            if cursor is not None:
                c["cursor"] = max(c.get("cursor", 0), cursor)
            if c["status"] != status or status.startswith("Needs attention"):
                self.db.log("Connection", c["label"] + " · " + status, connection=c["id"])
            c.update(
                status=status,
                next=self.now() + (1 if cursor and cursor < self.now() - 65 else c.get("interval", 60)),
            )
            self.db.put("connections", c)

    def discover(self, key, report=""):
        with self.event_locks[int(key[:4], 16) % len(self.event_locks)]:
            return self.classify(key, report)

    def classify(self, key, report):
        with self.db.atomic():
            e = self.db.get("events", key)
            if not e or e["state"] == "working" or (not report and e["state"] not in {"new", "ignored"}):
                return
            if self.scope.matches(e):
                return
            if (self.db.get("tasks", key) or {}).get("status") == "closed":
                e["state"] = "handled"
                self.db.put("events", e)
                return
            e["state"] = "working"
            self.db.put("events", e)
        r = e["raw"]
        try:
            projects = self.db.rows("projects")
            project_names = [p["name"] for p in projects]
            source = self.db.get("connections", e["connection"])
            if source and source["kind"] == "github" and not e["project"]:
                repo = source["config"]["repository"].casefold()
                matches = [
                    p["name"]
                    for p in projects
                    if not p.get("global")
                    and (any(bugflow.checkout_repository(path) == repo for path in self.project_paths(p))
                         or "github.com/" + repo in self.project_origins(p))
                ]
                parents = {p["name"]: p.get("parent") for p in projects}
                broader = {parent for name in matches for parent in self.ancestors(name, parents)}
                matches = [name for name in matches if name not in broader]
                if len(matches) == 1:
                    e["project"] = matches[0]
            if self.previously_sent(e):
                out = dict(owned=False, relevant=False, reason="Already delivered by this app")
            elif r.get("owned"):
                out = dict(owned=True, relevant=False, reason="Another person owns this work.")
            else:
                out = self.infer(
                    "discovery",
                    dict(
                        event=r,
                        thread=self.thread_context(e),
                        miss_report=report,
                        user_requested=e.get("user_requested", False),
                        now=self.now(),
                        projects=project_names,
                        project_groups={
                            p["name"]: p.get("parent") or self.global_project
                            for p in projects
                            if not p.get("global")
                        },
                        global_scope=self.global_project,
                        source=source["label"] if source else "",
                    ),
                    e["project"],
                    key,
                )
            inferred_paths = []
            if (out.get("relevant") and source and source["kind"] == "github" and not source.get("unavailable")
                    and e["project"] in {"", self.global_project} and not self.scope.matches(e)):
                try:
                    inferred_paths = self.repository_candidates({"project": self.global_project}, source)
                except bugflow.CheckoutAccessError:
                    pass  # Preparation can ask for access if the task needs this folder.
            with self.db.atomic():
                if self.db.get("events", key)["generation"] != e["generation"]:
                    return
                c = self.db.get("connections", e["connection"])
                if not c or c.get("unavailable"):
                    return
                t = self.db.get("tasks", key)
                if t:
                    e["project"] = t["project"]
                elif not e["project"]:
                    suggested = out.get("project")
                    e["project"] = suggested if suggested in project_names else self.global_project
                if self.scope.matches(e) or (t and t["status"] == "closed"):
                    e["state"] = "handled" if t and t["status"] == "closed" else "new"
                    self.db.put("events", e)
                    return
                if not t and (out.get("relevant") or out.get("owned")):
                    if len(inferred_paths) == 1 and not self.scope.path(inferred_paths[0]):
                        e["project"] = self.checkout_project(c, inferred_paths[0])
                    self.new_task(
                        out.get("title") or r["title"],
                        e["project"],
                        r["body"],
                        id=key,
                        source=r.get("context", {}).get("google_service") or c["kind"],
                        event=key,
                        url=r.get("url", ""),
                        kind=out.get("kind") or "task",
                        reason=out.get("reason", ""),
                        account=r["account"],
                        destination=r["destination"],
                        status="skipped" if out.get("owned") else "queued",
                    )
                elif t and out.get("owned") and t["status"] not in BUSY | TERMINAL:
                    t.update(status="skipped", draft="")
                    self.save(t, out.get("reason") or "Someone else is handling this")
                if (
                    out.get("relevant")
                    and not (r.get("owned") or out.get("owned"))
                    and not r.get("authored")
                    and r.get("context", {}).get("google_service") != "Calendar"
                ):
                    self.schedule_deadline(
                        key, e["project"], key, out.get("title") or r["title"], out.get("deadline")
                    )
                if r.get("authored"):
                    self.skill("style", out.get("style") or "", False, "Previously authored content")
                    self.followups(key, e["project"], out.get("commitments", []), r["destination"])
                e["state"] = "handled" if (out.get("relevant") or out.get("owned")) else "ignored"
                e["next"] = self.now() + 4 * 3600
                self.db.put("events", e)
                self.db.log("Discovery", out.get("reason", "Reviewed source"), e["project"], key)
        except Exception as exc:
            if isinstance(exc, worker.WorkerStopped):
                return  # the event is read again on restart
            with self.db.atomic():
                latest = self.db.get("events", key)
                if latest and latest["generation"] == e["generation"]:
                    e.update(state="ignored", next=self.now() + 300)
                    self.db.put("events", e)
                self.db.log("Discovery failed", str(exc), e["project"], key)
            if report:
                raise

    def previously_sent(self, e):
        if not e["raw"].get("authored"):
            return False
        receipt = connectors.authored_receipt(e["raw"])
        return bool(
            receipt
            and any(
                r.get("receipt") == receipt and r.get("connection") == e["connection"]
                for t in self.db.rows("tasks")
                for r in t["receipts"]
            )
        )

    @staticmethod
    def same_thread(e, other):
        if e["connection"] != other["connection"]:
            return False
        raw, candidate = e["raw"], other["raw"]
        a, b = raw.get("context", {}), candidate.get("context", {})
        if a.get("thread") and b.get("thread"):
            return a["thread"] == b["thread"]
        if candidate.get("destination") != raw.get("destination"):
            return False
        if "subject" in a:
            return a["subject"].removeprefix("Re: ") == b.get("subject", "").removeprefix("Re: ")
        return True

    def thread_context(self, e):
        records = sorted(
            (x["raw"] for x in self.db.rows("events") if x["id"] != e["id"]
             and self.same_thread(e, x) and not self.scope.matches(x)),
            key=lambda x: x["timestamp"],
            reverse=True,
        )
        return [
            dict(title=r["title"], body=r["body"][:1500], author=r.get("context", {}).get("author", ""))
            for r in records[:12]
        ]

    def followups(self, parent, project, promises, destination):
        for promise in promises or []:
            title = str(promise.get("title", "")).strip()
            if not title:
                continue
            key = digest(parent, title.casefold())
            if not self.db.get("tasks", key):
                due = promise.get("due")
                origin = self.db.get("tasks", parent) or {}
                event_id = origin.get("event") or parent
                event = self.db.get("events", event_id)
                c = self.db.get("connections", event["connection"]) if event else None
                self.new_task(
                    title,
                    project,
                    f"Fulfill the commitment to {destination}: {title}",
                    id=key,
                    parent=parent,
                    event=event_id if event else "",
                    source=c["kind"] if c else "Local",
                    account=event["raw"]["account"] if event else "",
                    kind="commitment",
                    status="scheduled" if due and due > self.now() else "queued",
                    due=due or 0,
                    destination=destination,
                )
                self.schedule_deadline(key, project, event_id if event else "", title, due)

    def schedule_deadline(self, parent, project, event_id, title, due):
        if type(due) not in (float, int) or not self.now() < due < 253402299899 or not self.calendars():
            return
        key = digest(parent, "calendar")
        if not self.db.get("tasks", key):
            self.new_task(
                "Calendar · " + title,
                project,
                "Schedule the deadline on the primary calendar.",
                id=key,
                parent=parent,
                event=event_id,
                kind="calendar",
                calendar_details=dict(title=title, start=due, end=due + 900),
            )

    def process(self, key):
        with self.db.atomic():
            t = self.db.get("tasks", key)
            if not t or t["status"] not in {"queued", "scheduled"} or t.get("due", 0) > self.now():
                return
            if not self.owns("task:" + key):
                return
            if self.scope.task(t):
                self.save(t, "Paused by the workspace ignore list")
                return
            t.update(status="working", local_commands_available=True)
            t.pop("related_snapshot", None)
            self.save(t, "Agent picked up task")
            version = t["version"]
        try:
            if t.get("special") == "self_check":
                checks = selfcheck.run()
            elif t.get("special") == "app_finding":
                update = selfcheck.prepare_finding(self, t)
            elif t.get("special") == "report":
                update = self.recover(t)
            elif t.get("special") == "style":
                response = self.infer(
                    "style", {"feedback": t["body"], "clarifications": t["feedback"]}, t["project"], key
                )
                with self.db.atomic():
                    self.require_current_task(t)
                    self.skill("style", response["body"], False, t["body"])
                if response.get("question"):
                    update = self.decision(t, response["question"])
                else:
                    update = dict(
                        status="done",
                        result="Style suggestion ready for review."
                        if response["body"]
                        else "No reusable writing preference was identified.",
                    )
            else:
                update = self.prepare(t)
            with self.db.atomic():
                latest = self.db.get("tasks", key)
                if latest["version"] != version:
                    return
                if not self.owns("task:" + key):
                    self.db.log("Result discarded", "Another instance owns this task now.", t["project"], key)
                    return
                if t.get("special") == "self_check":
                    update = selfcheck.record_results(self, t, checks)
                latest.update(update)
                latest.pop("agent_retries", None)
                self.save(latest, latest.get("question") or latest.get("reason") or latest["status"])
                if latest["status"] == "question" and latest.get("repository_question"):
                    # A registration may have changed while the decision agent was running.
                    self.repository_recheck.set()
        except Exception as exc:
            if isinstance(exc, worker.WorkerStopped):
                return  # the app is quitting; the task stays working and is queued again on restart
            with self.db.atomic():
                latest = self.db.get("tasks", key)
                if latest["version"] == version and self.owns("task:" + key):
                    attempts = latest.get("checkout_retries", 0)
                    if isinstance(exc, bugflow.CheckoutAccessError):
                        latest.update(status="question", question=str(exc), repository_question=True,
                                      checkout_inaccessible=True, reason="Waiting for access to the registered checkout")
                    elif isinstance(exc, worker.CheckoutTimeout) and attempts < 2:
                        delay = (30, 120)[attempts]
                        latest.update(status="scheduled", due=self.now() + delay, checkout_retries=attempts + 1,
                                      result=str(exc), reason=f"Retrying checkout access in {delay} seconds")
                    elif isinstance(exc, worker.AgentTimeout) and latest.get("agent_retries", 0) < 2:
                        attempt = latest.get("agent_retries", 0)
                        delay = (30, 120)[attempt]
                        latest.update(status="scheduled", due=self.now() + delay, agent_retries=attempt + 1,
                                      result=str(exc), reason=f"Retrying agent preparation in {delay} seconds")
                    else:
                        latest.update(status="failed", result=str(exc))
                        if isinstance(exc, (worker.AgentTimeout, worker.CheckoutTimeout)):
                            latest["reason"] = "Preparation timed out after two automatic retries"
                    self.save(latest, str(exc))
        self.wake.set()

    @staticmethod
    def calendar_answers(calendars):
        return [dict(label=email, detail="Use this calendar for the event.", value=email)
                for email in sorted(calendars) if len(email) <= 32][:3]

    @staticmethod
    def repository_answers(paths):
        folders = [Path(path) for path in paths]
        choices = []
        for index, folder in enumerate(folders[:3]):
            label = folder.name
            if any(other != folder and other.name == label for other in folders):
                label = folder.parent.name + "/" + label
            if len(label) > 32 or any(choice["label"] == label for choice in choices):
                label = f"Checkout {index + 1}"
            choices.append(dict(label=label, detail=str(folder), value=str(folder)))
        return choices

    def format_existing_question(self, target):
        key, version = target
        task = self.db.get("tasks", key)
        if not task or task["version"] != version or self.scope.task(task):
            return
        old = task["question"]
        if task.get("repository_question"):
            paths = self.repository_candidates(task, self.step_source(task))
            prompt = "Which local checkout should this task use?" if paths else "Where is this task’s local checkout?"
            choices = self.repository_answers(paths)
        elif task.get("calendar_question"):
            prompt = "Which calendar should receive this event?"
            choices = self.calendar_answers(self.calendars())
        else:
            response = self.infer("decision", {"question": old, "task": task, "dry": True}, task["project"], key)
            prompt, choices = response["question"], response.get("choices", [])
        with self.db.atomic():
            latest = self.db.get("tasks", key)
            if latest["version"] == version and not self.scope.task(latest):
                latest.update(question=prompt, question_context=old, answer_choices=choices)
                self.save(latest, "Prepared queue question and answer choices")

    def decision(self, t, question):
        response = self.infer(
            "decision",
            {"question": question, "task": t, "dry": self.settings()["dry"]},
            t["project"],
            t["id"],
        )
        q = response.get("question") or question
        category = response.get("kind") or t["kind"] or "decision"
        categories = {category, t["kind"]}
        if t["commitments"]:
            categories.add("commitment")
        authorized = all(self.rule(t["project"], kind) == "auto" for kind in categories)
        if not self.settings()["dry"] and authorized and response.get("answer"):
            answer = response["answer"]
            if not any(f == {"question": q, "answer": answer} for f in t["feedback"]):
                return dict(status="queued", feedback=t["feedback"] + [{"question": q, "answer": answer}])
        return dict(
            status="question",
            question=q,
            question_context=response.get("context") or (question if question != q else ""),
            answer_choices=response.get("choices", []),
            decision_kind=category,
            decision_id=digest(t["project"], " ".join(question.casefold().split())),
            reason="Waiting for your answer",
        )

    def save_prepared_files(self, task, edits, originals, *, partial):
        with self.db.atomic():
            latest = self.require_current_task(task)
            if self.scope.checkout(task["worktree"]):
                raise ValueError("This checkout belongs to an ignored repository.")
            changes = {edit["path"]: edit for edit in latest.get("changes", [])}
            changes.update((edit["path"], edit) for edit in edits)
            saved = list(changes.values())
            worker._validate_files(saved)
            paths = worker.apply_worktree_files(task["worktree"], edits, originals)
            latest.update(changes=saved, partial_changes=partial)
            self.db.put("tasks", latest)
            self.db.log("Files edited", "\n".join(paths), task["project"], task["id"])
            task.update(changes=saved, partial_changes=partial)

    def prepare(self, t):
        e = self.db.get("events", t.get("event", ""))
        conn = self.db.get("connections", e["connection"]) if e else None
        if e and (not conn or conn.get("unavailable")):
            raise ValueError("The source account is disconnected.")
        if t.get("sample"):
            return self.sample_prepare(t)
        if t.get("calendar_details") and not t.get("new_feedback"):
            return self.calendar_preview(t, e, t["calendar_details"])
        if self.rule(t["project"], t["kind"]) == "user" and not t["feedback"]:
            return self.decision(
                t, "Your project rule leaves this decision to you. How should this task be handled?"
            )
        if t.get("new_feedback"):
            preference = self.infer(
                "style", {"feedback": t["new_feedback"], "task": t["title"]}, t["project"], t["id"]
            )
            with self.db.atomic():
                self.require_current_task(t)
                self.skill("style", preference.get("body", ""), False, t["new_feedback"])
                if preference.get("question"):
                    ident = digest(t["id"], t["new_feedback"], "style clarification")
                    if not self.db.get("tasks", ident):
                        self.new_task(
                            "Clarify writing preference",
                            t["project"],
                            t["new_feedback"],
                            special="style",
                            parent=t["id"],
                            id=ident,
                            kind="style",
                        )
        files = {}
        location = t["worktree"]
        repository = t.get("repository", "")
        coding = t["kind"].lower() in {"coding", "bug", "code", "bugfix"}
        parent = self.step_parent(t)
        repo_source = self.step_source(t) if parent else conn
        github_issue = (conn and conn["kind"] == "github"
                        and e["raw"].get("context", {}).get("is_pull_request") is False)
        candidates = None
        if (github_issue or parent) and not location:
            candidates = self.repository_candidates(t, repo_source)
            if parent and not repository:
                repository = parent.get("repository", "")
            if repository not in candidates:
                repository = self.match_repository(t, repo_source, candidates)
        omissions = {}
        available = []
        hints = "\n".join((t.get("question", ""), t.get("result", ""), t["title"], t["body"],
                           parent["body"] if parent else "", e["raw"]["body"] if e else ""))
        if coding or location or ((github_issue or parent) and repository):
            if self.scope.checkout(repository or location):
                raise ValueError("This checkout belongs to an ignored repository.")
            if location and not repository:
                # Older tasks stored only the verified task worktree.
                location = str(worker.resume_worktree(location, self.db.root / "worktrees", t["id"]))
            elif location:
                location = str(worker.prepare_worktree(repository, self.db.root / "worktrees", t["id"],
                                                       base="remote", pull=self.pull_request_number(t),
                                                       token=self.github_token(self.origin_of(repository))))
            else:
                if candidates is None:
                    candidates = self.repository_candidates(t, repo_source)
                if repository not in candidates:
                    repository = self.match_repository(t, repo_source, candidates)
                if not repository:
                    question = (
                        "Which repository should this task use?"
                        if candidates
                        else "Where is this task’s local checkout?"
                    )
                    decision = self.decision(
                        t, question + ("\n" + "\n".join(candidates) if candidates else "")
                    )
                    if decision["status"] == "queued":
                        answer = decision["feedback"][-1]["answer"]
                        if answer in candidates:
                            return {**decision, "repository": answer}
                    return {
                        **decision,
                        "status": "question",
                        "question": decision.get("question") or question,
                        "repository_question": True,
                        "answer_choices": self.repository_answers(candidates),
                        "repository_options": candidates,
                        "decision_id": digest(t["id"], "repository"),
                    }
                with self.db.atomic():
                    latest = self.require_current_task(t)
                    latest["repository"] = repository
                    if self.scope.task(latest):
                        self.save(latest, "Selected repository is ignored")
                        return {}
                    self.db.put("tasks", latest)
                location = str(worker.prepare_worktree(repository, self.db.root / "worktrees", t["id"],
                                                       base="remote", pull=self.pull_request_number(t),
                                                       token=self.github_token(self.origin_of(repository))))
            t = dict(t, repository=repository, worktree=location,
                     github_repository=bugflow.checkout_repository(location))
            with self.db.atomic():
                latest = self.require_current_task(t)
                latest.update(repository=repository, worktree=location, github_repository=t["github_repository"])
                self.db.put("tasks", latest)
            files = worker.worktree_context(location, hints, omissions, available)
        family = [row for row in self.db.rows("tasks") if row["project"] == t["project"]]
        children = [row for row in family if row.get("parent") == t["id"]]
        siblings = [row for row in family if row.get("parent") == parent["id"]] if parent else []
        related = self.related_evidence(t, parent, children, siblings)
        context = dict(
            task=t,
            event=e["raw"] if e else {},
            files=files,
            omitted_files=omissions,
            available_paths=available,
            google_calendars=list(self.calendars()),
            now=self.now(),
            thread=self.thread_context(e) if e else [],
            children=[
                dict(title=x["title"], result=x["result"][:3000], status=x["status"])
                for x in children
            ],
        )
        if parent:
            context["parent_task"] = dict(id=parent["id"], title=parent["title"], body=parent["body"][:12000],
                                          answers=related["parent_answers"], result=related["parent_result"])
            context["completed_siblings"] = related["siblings"]
        if not location and not e and t["project"] != self.global_project:
            paths = self.repository_candidates(t, None)
            candidate = self.match_repository(t, None, paths)
            if candidate:
                context["registered_checkout"] = dict(path=candidate, github_repository=bugflow.checkout_repository(candidate))
        if (
            e
            and e["raw"].get("context", {}).get("google_service") == "Drive"
            and "Drive" in conn.get("google_services", [])
        ):
            if conn.get("workspace_profile"):
                context["document_excerpt"] = workspace_bridge.excerpt(
                    self.db.root, conn, e["raw"]["context"]
                )
            else:
                token = connectors._transport_secret(conn, connectors.validate("email", conn["config"]))
                context["document_excerpt"] = google_workspace.document_excerpt(e["raw"]["context"], token)
        if e:
            # Include only activity from the same project referenced by this thread.
            body = e["raw"]["body"]
            context["linked_activity"] = [
                x["raw"]
                for x in self.db.rows("events")
                if x["project"] == t["project"]
                and not self.scope.matches(x)
                and x["id"] != e["id"]
                and x["raw"].get("url")
                and x["raw"]["url"] in body
            ][:12]
            for source in self.db.rows("connections"):
                if (
                    source["kind"] != "github"
                    or not source["config"].get("repository")
                    or source.get("unavailable")
                    or self.scope.connection(source)
                ):
                    continue
                repo = source["config"]["repository"]
                pattern = (
                    r"https://github\.com/" + re.escape(repo) + r"/(issues|pull)/([1-9]\d*)(?=[/?#>\s|),.]|$)"
                )
                for kind, number in list(dict.fromkeys(re.findall(pattern, body, re.IGNORECASE)))[:4]:
                    endpoint = ("issues/" if kind.lower() == "issues" else "pulls/") + number
                    try:
                        item = connectors.github_request(source, "GET", endpoint)
                        linked = {
                            k: item.get(k)
                            for k in ("title", "state", "body", "html_url", "updated_at", "mergeable")
                        }
                        count = item.get("comments", 0)
                        if count:
                            comments = connectors.github_request(
                                source,
                                "GET",
                                f"issues/{number}/comments",
                                {"per_page": 3, "page": (count + 2) // 3},
                            )
                            linked["recent_comments"] = [c.get("body", "")[:2500] for c in comments]
                        context["linked_activity"].append(linked)
                        self.db.log(
                            "Linked activity read",
                            json.dumps(linked, ensure_ascii=False),
                            t["project"],
                            t["id"],
                        )
                    except Exception as exc:
                        self.db.log("Linked activity unavailable", str(exc), t["project"], t["id"])
                        context.setdefault("missing_context", []).append(f"{repo}#{number}: {exc}")
        bug = t["kind"].lower() in {"bug", "bugfix"}
        if bug and t.get("changes") and t.get("milestones") and not t.get("partial_changes"):
            out = dict(outcome="done", text=t["result"], kind="bug")
        else:
            out = self.infer("task", context, t["project"], t["id"])
            if location and out["outcome"] == "question":
                prepared = out.get("files") or []
                if prepared:
                    self.save_prepared_files(t, prepared, files, partial=True)
                requested_omissions = {}
                extra = worker.worktree_context(location, out["text"] + "\n" + hints, requested_omissions)
                new_context = extra.keys() - files.keys() - {edit["path"] for edit in prepared}
                if new_context or requested_omissions.keys() - omissions.keys():
                    files = extra
                    followup = dict(context, files=files, omitted_files=requested_omissions, file_request=out["text"])
                    out = self.infer("task", followup, t["project"], t["id"])
                    if out["outcome"] == "question" and out.get("files"):
                        self.save_prepared_files(t, out["files"], files, partial=True)
        result = out["text"]
        update = dict(
            result=result,
            worktree=location,
            repository=repository,
            repository_question=False,
            context_checked=True,
            checkout_retries=0,
            new_feedback="",
            question="",
            draft="",
            commitments=[],
            due=0,
        )
        outcome = out["outcome"]
        newkind = out.get("kind") or t["kind"]
        needs_checkout = outcome == "question" and re.search(r"\b(worktree|(?:git|local|registered) checkout)\b", result, re.I)
        if not location and (out.get("files") or needs_checkout) and newkind.lower() not in {"bug", "bugfix"}:
            newkind = "coding"
        if not coding and newkind.lower() in {"coding", "code", "bug", "bugfix"}:
            return dict(
                update, status="queued", kind=newkind, reason="Preparing a dedicated coding workspace"
            )
        if out.get("calendar_event") and outcome == "draft":
            return {**update, **self.calendar_preview(t, e, out["calendar_event"])}
        if outcome == "draft" and e and e["raw"].get("context", {}).get("google_service"):
            return {
                **update,
                **self.decision(t, "Which mail conversation or calendar event should this activity lead to?"),
            }
        if outcome == "skip":
            return dict(update, status="skipped", reason=result)
        if out.get("style") and (t["feedback"] or (e and e["raw"].get("authored"))):
            with self.db.atomic():
                self.require_current_task(t)
                self.skill("style", out["style"], False, "Task agent suggestion")
        if outcome == "question":
            with self.db.atomic():
                self.require_current_task(t)
                remaining = max(0, 6 - sum(bool(child.get("depth")) for child in children))
                for step in out.get("independent_steps", []):
                    title = required(step, "title", 300)
                    ident = digest(t["id"], title)
                    if remaining and not self.db.get("tasks", ident) and t.get("depth", 0) < 2:
                        self.new_task(
                            title,
                            t["project"],
                            required(step, "body"),
                            id=ident,
                            parent=t["id"],
                            depth=t.get("depth", 0) + 1,
                        )
                        remaining -= 1
            return {**update, "related_snapshot": digest(related), **self.decision(t, result)}
        if out.get("files"):
            if not location:
                return {
                    **update,
                    **self.decision(t, "Which local Git repository should be linked to this project?"),
                }
            self.save_prepared_files(t, out["files"], files, partial=False)
        update["partial_changes"] = False
        if not bug and outcome == "done" and github_issue and coding and t.get("changes"):
            # Finished edits for an open issue ship as a fix branch and PR, never as a silent "done".
            bug = True
            update["kind"] = "bug"
        if bug:
            github = self.github_for({**t, **update}, conn)
            if not github or not (update.get("changes") or t.get("changes")):
                return {
                    **update,
                    **self.decision(
                        t,
                        "The fix needs a connected GitHub repository and worktree changes. Which repository and fix should I use?",
                    ),
                }
            change = bugflow.prepare(
                {**t, **update}, e, github, connectors.github_request, connectors.github_identity
            )
            if change:
                return {**update, **change, "github": github["id"]}
            if not e:
                return {
                    **update,
                    **self.decision(
                        t, "Which connected source thread should receive the reporter notification?"
                    ),
                }
            url = t["milestones"]["url"]
            if e["raw"].get("authored"):
                # The user reported it, so the verified PR is the whole outcome; no notice to themselves.
                return dict(update, status="done", kind="bug", result=result.rstrip() + "\n\nPull request: " + url)
            canonical = bugflow.reporter_message(e, url)
            notice = self.infer(
                "task",
                {
                    "instruction": "Prepare a short reporter notification in approved style using the verified PR URL. Preserve any reporter mention. Return a draft or a clarification question.",
                    "verified_message": canonical,
                    "task": t,
                    "event": e["raw"],
                },
                t["project"],
                t["id"],
            )
            if notice["outcome"] == "question":
                return {**update, **self.decision(t, notice["text"])}
            result = notice["text"]
            if url not in result:
                result += " " + url
            mention = canonical.partition("The fix is ready")[0].strip()
            if mention and mention not in result:
                result = mention + " " + result
            out["commitments"] = notice.get("commitments", [])
            outcome = "draft"
            out.update(complete_after_send=True)
            newkind = "bug"
        if outcome == "done":
            if e and conn["kind"] == "email" and not e["raw"].get("authored") and not t["receipts"]:
                return {
                    **update,
                    **self.decision(t, "This email has no delivered reply. Does it need a response?"),
                }
            return dict(update, status="done")
        if not e:
            return {
                **update,
                **self.decision(
                    t, "This task has no connected destination. Should I keep this as a local result?"
                ),
            }
        subject = e["raw"].get("context", {}).get("subject", "") if conn["kind"] == "email" else ""
        if subject and not subject.casefold().startswith("re:"):
            subject = "Re: " + subject
        return dict(
            update,
            status="review",
            draft=result,
            subject=subject,
            account=e["raw"]["account"],
            destination=e["raw"]["destination"],
            operation="reply",
            kind=newkind,
            categories=["reply", newkind],
            commitments=out.get("commitments", []),
            complete_after_send=out.get("complete_after_send", False),
            next_step=out.get("next_step", ""),
        )

    def repository_candidates(self, t, conn, origins=None):
        paths = self.repositories(t["project"], clone=True)
        github = conn and conn["kind"] == "github"
        slug = conn["config"].get("repository", "").casefold() if github else ""
        if github and not slug:
            return []
        if not github and not self.scope.slugs:
            return paths
        origins = {} if origins is None else origins
        matches = []
        access_error = None
        for path in paths:
            if path not in origins:
                try:
                    origins[path] = bugflow.checkout_repository(path, report_access=True)
                except bugflow.CheckoutAccessError as exc:
                    origins[path] = exc
            origin = origins[path]
            if isinstance(origin, bugflow.CheckoutAccessError):
                access_error = origin
            elif origin not in self.scope.slugs and (not github or origin == slug):
                matches.append(path)
        if github and not matches and not conn.get("unavailable") and not self.scope.connection(conn):
            slug = connectors.validate("github", conn["config"])["repository"].casefold()
            key = ("local", slug)
            if key not in origins:
                home = Path.home()
                roots = [Path(p) for p in self.suggestions.config()["folders"]]
                roots.extend(Path(p).parent for p in self.repositories(self.global_project))
                roots.extend(home / name for name in ("Developer", "Projects", "src", "code", "Documents"))
                storage = self.db.root.resolve()
                def excluded(path):
                    return path == storage or storage in path.parents or self.scope.path(path)
                origins[key] = [path for path in bugflow.checkout_folders(slug.split("/")[1], roots, excluded)
                                if path not in origins and bugflow.checkout_repository(path) == slug]
            registered = set(self.repositories(self.global_project))
            matches = [path for path in origins[key] if path not in registered or path in paths]
        if access_error and (not matches or not github):
            raise access_error
        return matches

    def match_repository(self, t, conn, paths):
        if len(paths) == 1 and conn and conn["kind"] == "github":
            return paths[0]
        evidence = t["title"] + "\n" + t["body"]

        def mentioned(value):
            return value and re.search(r"(?<![\w./-])" + re.escape(value) + r"(?![\w.-])", evidence, re.I)

        matches = [p for p in paths if mentioned(p)]
        if not matches:
            matches = [
                p
                for p in paths
                if (slug := bugflow.checkout_repository(p))
                and (mentioned(slug) or mentioned("https://github.com/" + slug))
            ]
        if not matches:
            matches = [p for p in paths if mentioned(Path(p).name)]
        if (
            not matches
            and len(paths) == 1
            and t["project"] != self.global_project
            and "github.com/" not in evidence.casefold()
        ):
            return paths[0]
        return matches[0] if len(matches) == 1 else ""

    def github_for(self, t, conn):
        if t.get("github"):
            return self.db.get("connections", t["github"])
        if conn and conn["kind"] == "github":
            return conn
        path = t.get("worktree") or t.get("repository")
        if not path:
            paths = self.repositories(t["project"])
            path = paths[0] if len(paths) == 1 and t["project"] != self.global_project else ""
        origin = bugflow.checkout_repository(path)
        matches = [
            c
            for c in self.db.rows("connections")
            if origin
            and c["kind"] == "github"
            and not c.get("unavailable")
            and c["config"].get("repository", "").casefold() == origin
        ]
        return matches[0] if len(matches) == 1 else None

    def recover(self, t):
        before = {x["id"] for x in self.db.rows("tasks")}
        end = t["created"]
        start = end - 10800
        gaps = []
        errors = []
        for account in self.db.rows("connections"):
            if account.get("auth_method") in {"gh_account", "slack_workspace"}:
                try:
                    self.sync(account, (start, end))
                except Exception as exc:
                    errors.append(account["label"] + ": " + str(exc))
                    gaps.append(errors[-1])
        for c in self.db.rows("connections"):
            if c.get("auth_method") in {"gh_account", "slack_workspace"} or c.get("unavailable"):
                continue
            try:
                self.sync(c, (start, end))
                gap = connectors.coverage_gap(c)
                if gap:
                    gaps.append(c["label"] + ": " + gap)
            except Exception as exc:
                errors.append(c["label"] + ": " + str(exc))
                gaps.append(errors[-1])
        events = [e for e in self.db.rows("events") if start <= e["raw"]["timestamp"] <= end and not self.scope.matches(e)]
        current = (self.db.root / "skills" / "workflow.md").read_text()
        if not events:
            why = " ".join(errors) or "No source events were available in the three-hour window."
            return {
                **self.decision(t, why + " Which source or example should I use to investigate the miss?"),
                "result": why,
                "recovered": [],
            }
        response = self.infer(
            "improvement",
            dict(
                report=t["body"],
                clarifications=t["feedback"],
                workflow=current,
                examples=[
                    {"title": e["raw"]["title"], "body": e["raw"]["body"][:1000], "previous": e["state"]}
                    for e in events[:40]
                ],
            ),
            t["project"],
            t["id"],
        )
        # Exercise the proposed rule against recent evidence before activating it.
        candidate = next((e for e in events if not e["raw"].get("owned")), events[0])
        trial = self.infer(
            "discovery",
            dict(
                event=candidate["raw"],
                miss_report=t["body"],
                proposed_workflow=response["body"],
                instruction="Classify this example using the proposed workflow.",
            ),
            candidate["project"],
            t["id"],
        )
        self.db.log(
            "Skill replay",
            candidate["raw"]["title"] + " · " + trial["reason"],
            candidate["project"],
            t["id"],
        )
        skipped = next((e for e in events if e["raw"].get("owned")), None)
        if skipped:
            self.db.log(
                "Skill replay",
                "Ownership check preserved: " + skipped["raw"]["title"],
                skipped["project"],
                t["id"],
            )
        # A revision must keep accepting work the user already accepted.
        accepted = [
            e for e in (
                self.db.get("events", x.get("event", ""))
                for x in sorted(self.db.rows("tasks"), key=lambda x: -x["created"])
                if x["status"] == "done" and x.get("event") and x["event"] != candidate["id"]
            )
            if e
        ][:2]
        for example in accepted:
            check = self.infer(
                "discovery",
                dict(
                    event=example["raw"],
                    proposed_workflow=response["body"],
                    instruction="Classify this previously accepted example using the proposed workflow.",
                ),
                example["project"],
                t["id"],
            )
            if not check.get("relevant"):
                failure = f"Rejected accepted example: {example['raw']['title']} · {check.get('reason', '')}"
                self.db.log("Skill check failed", failure, example["project"], t["id"])
                with self.db.atomic():
                    self.db.put("skills", dict(
                        id=uid(), kind="workflow", body=response["body"], active=False,
                        status="failed check", at=self.now(), reason=failure,
                    ))
                return {
                    **self.decision(
                        t,
                        failure + ". The current discovery revision stays active. "
                        "Which behavior should the revision change, and which should it keep?",
                    ),
                    "result": failure,
                    "recovered": [],
                }
            self.db.log("Skill check passed", example["raw"]["title"], example["project"], t["id"])
        with self.db.atomic():
            if self.db.get("tasks", t["id"])["version"] != t["version"]:
                raise ValueError("The investigation changed before activation.")
            if (self.db.root / "skills" / "workflow.md").read_text() != current:
                raise ValueError(
                    "Discovery instructions changed during investigation. Retry with the active revision."
                )
            self.skill("workflow", response["body"], True, response.get("reason", "Recovery report"))
            self.write_markdown()
        for e in events:
            try:
                self.discover(e["id"], t["body"])
            except Exception as exc:
                gaps.append(e["raw"]["title"] + ": " + str(exc))
        recovered = [e["id"] for e in events if e["id"] not in before and self.db.get("tasks", e["id"])]
        window = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start))
            + " to "
            + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(end))
        )
        t["recovered"] = recovered
        t["result"] = (
            f"{window} · all projects · {len(events)} events · {len(recovered)} recovered.\n"
            + response.get("reason", "")
        )
        if gaps:
            t["result"] += "\nCoverage gaps:\n" + "\n".join(gaps)
        self.db.log("Recovery complete", t["result"] + "\n" + "\n".join(recovered), t["project"], t["id"])
        update = dict(status="done", result=t["result"], recovered=recovered)
        if errors:
            update.update(
                self.decision(
                    t,
                    "Some sources could not be checked: "
                    + "; ".join(errors)
                    + ". Which access or connection should be restored?",
                )
            )
        return update

    def autosend(self, pair):
        try:
            self.deliver(*pair, automatic=True)
        except ValueError:
            pass

    def approved_send(self, pair):
        """Send a version the user approved on an instance that does not own the task."""
        try:
            self.deliver(*pair, approved_elsewhere=True)
        except ValueError:
            pass

    def approve(self, key, version):
        if self.owns("task:" + key):
            self.deliver(key, version)
            return None
        with self.dispatch_lock, self.db.atomic():
            t = self.db.get("tasks", key)
            if not t or t["version"] != version or t["status"] != "review":
                raise ValueError("This preview changed. Review its current version.")
            self.db.put("tasks", {**t, "approved_version": version})
            owner = self.instances.owner_name("task:" + key)
            self.db.log("Approved", f"Version {version}. {owner} owns this task and sends it.", t["project"], key)
        self.instances.wake.set()
        return {"relayed_to": owner}

    def deliver(self, key, version, automatic=False, approved_elsewhere=False):
        with self.dispatch_lock:
            with self.db.atomic():
                t = self.db.get("tasks", key)
                if not t or t["version"] != version or t["status"] != "review":
                    raise ValueError("This preview changed. Review its current version.")
                if not self.owns("task:" + key):
                    raise ValueError("Another instance owns this task and sends for it.")
                if approved_elsewhere and t.get("approved_version") != version:
                    raise ValueError("This version was not approved.")
                if self.scope.task(t):
                    raise ValueError("This project or repository is ignored.")
                if automatic and not self.auto(t):
                    raise ValueError("This action needs review.")
                if not t["draft"] or not t["account"] or not t["destination"]:
                    raise ValueError("Incomplete action preview.")
                e = self.db.get("events", t.get("event", ""))
                if e and e["raw"].get("owned"):
                    raise ValueError("Someone else owns this work.")
                conn = self.db.get("connections", e["connection"]) if e else None
                if t.get("operation") == "calendar_event":
                    conn = self.db.get("connections", t.get("google", ""))
                elif t.get("operation", "reply") != "reply":
                    conn = self.db.get("connections", t.get("github", ""))
                if self.scope.connection(conn) or self.scope.checkout(t.get("repository") or t.get("worktree")):
                    raise ValueError("This repository is ignored.")
                if (not conn or conn.get("unavailable")) and not t.get("sample"):
                    raise ValueError("Reconnect the source before delivery.")
                t["status"] = "sending"
                # The send intent: survives a crash as status "uncertain" and travels with the task.
                record = self.instances.owner("task:" + key)
                t["send_intent"] = dict(version=version, epoch=record["epoch"] if record else 0,
                                        instance=self.instances.id, at=self.now())
                self.save(
                    t,
                    ("Rule authorized" if automatic else "User confirmed")
                    + f" version {version}\n"
                    + t["draft"],
                )
            milestones = {}
            try:
                if t.get("sample"):
                    receipt = "Local demo receipt " + uid()[:8]
                elif t.get("operation") == "calendar_event":
                    receipt = (
                        workspace_bridge.create_event(self.db.root, conn, t)
                        if conn.get("workspace_profile")
                        else google_workspace.create_event(conn, t)
                    )
                elif t.get("operation", "reply") == "reply":
                    if e["raw"]["account"] != t["account"] or e["raw"]["destination"] != t["destination"]:
                        raise connectors.Rejected(
                            "Source no longer matches the account and destination preview."
                        )
                    if t.get("kind") in {"bug", "bugfix"} and t.get("milestones", {}).get("pr"):
                        github = self.github_for(t, conn)
                        try:
                            url = bugflow.verify_fix(t, github, connectors.github_request)
                        except Exception as exc:
                            raise connectors.Rejected(
                                "Unable to verify the fix before notification: " + str(exc)
                            ) from exc
                        mention = (
                            bugflow.reporter_message(e, url).partition("The fix is ready")[0].strip()
                            if url
                            else ""
                        )
                        if not url or url not in t["draft"] or (mention and mention not in t["draft"]):
                            raise connectors.Rejected(
                                "Reporter notification must include the verified pull request."
                            )
                    receipt = (
                        workspace_bridge.reply(self.db.root, conn, e["raw"], t["draft"])
                        if conn.get("workspace_profile")
                        else connectors.send(conn, e["raw"], t["draft"])
                    )
                else:
                    receipt, milestones = bugflow.send(
                        t, conn, connectors.github_request, connectors.github_identity
                    )
            except Exception as exc:
                with self.db.atomic():
                    t["status"] = "failed" if isinstance(exc, connectors.Rejected) else "uncertain"
                    t["result"] = str(exc)
                    self.save(t, str(exc))
                raise ValueError(f"{t['status']}: {exc}") from exc
            with self.db.atomic():
                self.finish_delivery(t, str(receipt), milestones)
            self.wake.set()

    def finish_delivery(self, t, receipt, milestones):
        event = self.db.get("events", t.get("event", ""))
        connection_id = (
            (event["connection"] if event else "")
            if t.get("operation", "reply") == "reply"
            else t.get("google")
            if t.get("operation") == "calendar_event"
            else t.get("github", "")
        )
        t["receipts"].append(
            dict(
                connection=connection_id,
                operation=t.get("operation", "reply"),
                receipt=receipt,
                text=t["draft"],
                at=self.now(),
            )
        )
        t.setdefault("milestones", {}).update(milestones)
        self.followups(t["id"], t["project"], t["commitments"], t["destination"])
        if milestones or not t.get("complete_after_send", True):
            t.update(status="queued", reason=t.get("next_step") or "Check remaining milestones")
        else:
            t.update(status="done", result=receipt + "\n" + t["draft"])
        t["draft"] = ""
        self.save(t, receipt, connection=connection_id)

    def sample_prepare(self, t):
        if t.get("sample_kind") == "question" and not t["feedback"]:
            return dict(status="question", question="Which release date should I use in the update?")
        reply = "The export fix is ready for review. The linked PR now handles empty rows and its checks are passing."
        if t["feedback"]:
            reply += "\n\n" + str(t["feedback"][-1].get("answer") or t["feedback"][-1].get("text", ""))
        return dict(
            status="review",
            draft=reply,
            operation="reply",
            account="Demo account",
            destination="#release · local sample",
            reason="Sample reply ready",
            categories=["reply"],
            commitments=[],
        )

    def action(self, d):
        op = required(d, "op", 40)
        key = d.get("id", "")
        if op.startswith("auth_"):
            return self.signins.action(d)
        if op == "orchestrator_prompt":
            return self.orchestrator.ask(d.get("text"))
        if op == "orchestrator_settings":
            return self.orchestrator.configure(d)
        if op == "agent_sign_in":
            return self.agents.open_sign_in(required(d, "agent", 16))
        if op == "hermes_install":
            self.agents.hermes.ensure_async()
            return
        if op == "hermes_dashboard":
            return self.agents.open_dashboard()
        if op.startswith("setup_") or op.startswith("vps_"):
            return self.setup_action(op, d)
        if op == "check_folder_access":
            path = required(d, "path")
            if not any(path in p["repos"] for p in self.db.rows("projects")):
                raise ValueError("Choose a folder registered under Projects.")
            worker.check_checkout_root(path)
            self.repository_recheck.set()
            self.wake.set()
            return
        if op == "connect_app_repo":
            login = github_signin.active_login()
            account = digest("github.com account", login.casefold())
            repo = selfcheck.REPOSITORY
            source = dict(id=digest(account, repo.casefold()), kind="github", label=repo,
                          config={"repository": repo}, identity=login, auth_method="gh_repository",
                          status="Connected", next=0, revision=uid())
            info = connectors.github_request(source, "GET", "")
            if info.get("full_name", "").casefold() != repo.casefold():
                raise ValueError("Could not verify the app repository.")
            with self.dispatch_lock, self.db.atomic():
                if not any(c["kind"] == "github" and not c.get("unavailable")
                           and c["config"].get("repository", "").casefold() == repo.casefold()
                           for c in self.db.rows("connections")):
                    self.db.put("connections", source)
                    self.db.log("Repository connected", repo, connection=source["id"])
            self.wake.set()
            return
        if op == "chat_source_config":
            return self.suggestions.configure_chat(d)
        if op == "suggestion_config":
            self.suggestions.configure(d)
            return
        if op == "suggestion_scan":
            self.suggestions.next_scan = 0
            self.wake.set()
            return
        if op in {"suggestion_start", "suggestion_dismiss"}:
            self.suggestions.decide(key, op == "suggestion_start")
            return
        if op in {"github_browse", "github_review_item"}:
            account = self.db.get("connections", key)
            if not account or account.get("auth_method") != "gh_account":
                raise ValueError("Connect the GitHub CLI account first.")
            if op == "github_browse":
                return connectors.github_open_work(account, d.get("view"), d.get("page", 1))
            source = next(
                (
                    c
                    for c in self.db.rows("connections")
                    if c.get("github_parent") == key
                    and not c.get("unavailable")
                    and c["config"]["repository"].casefold() == str(d.get("repo", "")).casefold()
                ),
                None,
            )
            if not source:
                raise ValueError(
                    "This repository is outside the connected inventory. Sync the account first."
                )
            if self.scope.connection(source):
                raise ValueError("This repository is ignored.")
            raw = connectors.github_selected_issue(source, d.get("number"))
            event_id = digest(source["id"], raw["key"])
            with self.db.atomic():
                current = self.db.get("connections", source["id"])
                if (
                    not current
                    or current.get("unavailable")
                    or current.get("revision") != source.get("revision")
                ):
                    raise ValueError("The source changed. Refresh the account first.")
                self.ingest(source, raw)
                event = self.db.get("events", event_id)
                if not event or self.scope.matches(event):
                    raise ValueError("This item belongs to an ignored scope.")
                if not self.db.get("tasks", event_id):
                    event.update(state="new", user_requested=True, generation=event["generation"] + 1)
                    self.db.put("events", event)
                self.wake.set()
            return {"event": event_id}
        if op == "approve":
            return self.approve(key, d.get("version"))
        if op.startswith("instance_") or op == "move":
            return self.instance_action(op, d)
        if op == "connect":
            self.connect(d)
            self.wake.set()
            return
        if op in {"context_retire", "context_restore"}:
            self.context.retire(key, d.get("reason") or "Retired in the dashboard", "user", op == "context_restore")
            return
        if op == "schedule_cancel":
            self.context.cancel_schedule(key, d.get("reason"), "user")
            return
        with self.dispatch_lock, self.db.atomic():
            if op in {"ignorelist", "set_exclusion"}:
                projects = self.db.rows("projects")
                if op == "set_exclusion":
                    field = {"project": "projects", "repository": "repositories"}.get(required(d, "scope"))
                    if not field or type(d.get("ignored")) is not bool:
                        raise ValueError("Choose a project or repository and its ignore state.")
                    change = {"projects": [], "repositories": []}
                    change[field] = [required(d, "value")]
                    value, = workscope.normalize(change, projects)[field]
                    entries = set(self.scope.config[field])
                    if d["ignored"]:
                        entries.add(value)
                    else:
                        entries.discard(value)
                    config = {**self.scope.config, "id": "ignorelist", field: sorted(entries)}
                else:
                    config = workscope.normalize(d, projects)
                if config == self.scope.config:
                    return
                self.db.put("config", config)
                self.refresh_scope()
                self.db.log("Ignore list updated", "Updated excluded projects and repositories")
            elif op in {"self_check", "app_finding"}:
                if not self.db.get("projects", selfcheck.PROJECT):
                    local = Path(__file__).resolve().parent
                    paths = []
                    if (local / ".git").exists() and bugflow.checkout_repository(local) == selfcheck.REPOSITORY.casefold():
                        paths.append(str(local))
                    self.add_project(selfcheck.PROJECT, paths, self.global_project)
                if op == "self_check":
                    if not any(t.get("special") == op and t["status"] in {"queued", "working"}
                               for t in self.db.rows("tasks")):
                        self.new_task("Test MeMyselfAI", selfcheck.PROJECT, source="App testing", special=op)
                else:
                    self.new_task(required(d, "title", 300), selfcheck.PROJECT, required(d, "body", 12000),
                                  source="App testing", kind="bug", special=op)
            elif op == "task":
                project = required(d, "project", 180) if d.get("project") else self.global_project
                if not self.db.get("projects", project):
                    raise ValueError("Choose an existing project.")
                self.new_task(required(d, "title", 1000), project, str(d.get("body", "")))
            elif op in {"report", "style"}:
                body = required(d, "text")
                self.new_task(
                    ("Recover missed work" if op == "report" else "Learn a writing preference"),
                    d.get("project") or self.global_project,
                    body,
                    special=op,
                    kind="recovery" if op == "report" else "style",
                )
            elif op == "project":
                self.add_project(required(d, "name", 180), d.get("repos", d.get("repo")), d.get("parent"))
            elif op == "rule":
                project = required(d, "project", 180)
                kind = required(d, "kind", 120).lower()
                mode = d.get("mode")
                if not self.db.get("projects", project) or mode not in {"auto", "review", "user"}:
                    raise ValueError("Choose a project and handling rule.")
                self.db.put("rules", dict(id=digest(project, kind), project=project, kind=kind, mode=mode))
                self.db.log("Rule changed", kind + " · " + mode, project)
            elif op == "settings":
                s = self.settings()
                if "dry" in d and type(d["dry"]) is not bool:
                    raise ValueError("Invalid dry mode setting.")
                if "provider" in d:
                    provider = required(d, "provider", 16)
                    if provider not in worker.PROVIDERS:
                        raise ValueError("Choose Claude Code or Codex CLI.")
                    s["provider"] = provider
                if "orchestrator_subscription" in d:
                    if d["orchestrator_subscription"] not in agents.SUBSCRIPTIONS:
                        raise ValueError("Choose the Claude or ChatGPT subscription for the orchestrator.")
                    s["orchestrator_subscription"] = d["orchestrator_subscription"]
                if "slots" in d:
                    if type(d["slots"]) is not int or not 0 <= d["slots"] <= 64:
                        raise ValueError("Enter between 0 and 64 agent slots, or 0 for the default.")
                    s["slots"] = d["slots"]
                if "preference" in d:
                    if d["preference"] not in {"first", "overflow", "never"}:
                        raise ValueError("Choose where tasks should run.")
                    s["preference"] = d["preference"]
                s["dry"] = d.get("dry", s["dry"])
                self.db.put("config", s)
                self.agents.cache = None
                self.db.log("Settings", f"Dry mode {'on' if s['dry'] else 'off'} · {s['provider']}")
            elif op == "sync":
                for c in self.db.rows("connections"):
                    c["next"] = 0
                    self.db.put("connections", c)
                for e in self.db.rows("events"):
                    if e["state"] == "ignored":
                        e["next"] = 0
                        self.db.put("events", e)
            elif op == "disconnect":
                saved = self.db.get("connections", key)
                if not saved:
                    raise ValueError("Connection has already been removed.")
                if saved.get("workspace_profile"):
                    workspace_bridge.forget(self.db.root, saved["workspace_profile"])
                elif saved.get("auth_method") == "slack_workspace":
                    keys = {saved["credential_key"]}
                    for child in self.db.rows("connections"):
                        if child.get("slack_parent") == key or (
                            child.get("auth_method") == "slack" and child.get("identity") == saved["identity"]
                        ):
                            keys.add(child.get("credential_key", child["id"]))
                            self.db.delete("connections", child["id"])
                    for cred in keys:
                        connectors.delete_secret(cred)
                elif saved.get("slack_parent"):
                    raise ValueError("Manage channels through their Slack workspace.")
                elif saved.get("auth_method") == "gh_account":
                    for child in self.db.rows("connections"):
                        if child.get("github_parent") == key:
                            if child.get("auth_method") == "github_cli":
                                connectors.delete_secret(child["id"])
                            self.db.delete("connections", child["id"])
                elif saved.get("github_parent"):
                    raise ValueError("Manage this repository through its GitHub account.")
                elif saved.get("credential_key"):
                    self.release_shared_credential(saved)
                elif saved.get("auth_method") != "gh_repository":
                    connectors.delete_secret(key)
                self.db.delete("connections", key)
                self.db.log("Disconnected", saved["label"], connection=key)
            elif op in {"accept_skill", "revert_skill", "reject_skill"}:
                s = self.db.get("skills", key)
                if not s:
                    raise ValueError("Select a skill revision.")
                if op in {"accept_skill", "reject_skill"} and (
                    s["kind"] != "style" or s.get("status") != "pending"
                ):
                    raise ValueError("Workflow improvements are applied by recovery.")
                if op == "revert_skill" and not s["active"]:
                    raise ValueError("This revision is inactive.")
                if op == "revert_skill" and s["kind"] == "workflow":
                    older = [
                        r for r in self.db.rows("skills") if r["kind"] == "workflow" and r["at"] < s["at"]
                    ]
                    if not older:
                        raise ValueError("The initial discovery skill has no previous revision.")
                    prev = max(older, key=lambda x: x["at"])
                    prev.update(active=True, status="active")
                    self.db.put("skills", prev)
                s["active"] = op == "accept_skill"
                s["status"] = {
                    "accept_skill": "active",
                    "revert_skill": "reverted",
                    "reject_skill": "rejected",
                }[op]
                self.db.put("skills", s)
                self.db.log("Skill " + s["status"], s["body"])
                self.write_markdown()
            elif op == "demo":
                if not any(t.get("sample") for t in self.db.rows("tasks")):
                    self.add_project("Sample workspace", "")
                    for title, kind in [
                        ("Reply to the release thread", "draft"),
                        ("Clarify the release date", "question"),
                    ]:
                        self.new_task(
                            title,
                            "Sample workspace",
                            "Synthetic activity for exploring the local approval flow.",
                            source="Sample",
                            kind="reply",
                            sample=True,
                            sample_kind=kind,
                        )
            elif op in {"feedback", "answer", "retry", "dismiss", "close_task", "reconcile"}:
                t = self.db.get("tasks", key)
                if not t:
                    raise ValueError("Task was not found.")
                if t["status"] in BUSY and op != "reconcile":
                    raise ValueError("Resolve the delivery outcome first.")
                if op == "reconcile":
                    if (
                        t["status"] != "uncertain"
                        or t["version"] != d.get("version")
                        or type(d.get("delivered")) is not bool
                    ):
                        raise ValueError("Review the current uncertain delivery.")
                    if d["delivered"]:
                        milestones = {}
                        receipt = "Delivery verified by user"
                        if t.get("operation") == "publish branch":
                            conn = self.db.get("connections", t["github"])
                            milestones = bugflow.reconcile_branch(t, conn, connectors.github_request)
                        elif t.get("operation") in {"create issue", "create pull request"}:
                            number = int(d.get("number") or 0)
                            if number <= 0:
                                raise ValueError("Enter the verified issue or pull request number.")
                            conn = self.db.get("connections", t["github"])
                            typ = "issue" if t["operation"] == "create issue" else "pr"
                            milestones = {typ: number, "repository": conn["config"]["repository"]}
                            if typ == "pr":
                                milestones["url"] = (
                                    f"https://github.com/{conn['config']['repository']}/pull/{number}"
                                )
                        elif t.get("operation") == "submit PR review":
                            number = int(d.get("number") or 0)
                            if number <= 0:
                                raise ValueError("Enter the GitHub review ID.")
                            conn = self.db.get("connections", t["github"])
                            review = connectors.github_request(conn, "GET", f"pulls/{t['milestones']['pr']}/reviews/{number}")
                            receipt = bugflow.review_receipt(t, review)
                        self.finish_delivery(t, receipt, milestones)
                    else:
                        t["status"] = "review"
                        self.save(t, "User verified absence of write")
                elif op == "close_task":
                    if t["status"] != "closed":
                        t.update(status="closed", draft="", question="", reason="Closed by you")
                        self.save(t, "Closed by user")
                elif t["status"] == "closed":
                    raise ValueError("This task is closed.")
                elif op == "dismiss":
                    t.update(status="dismissed", draft="", question="")
                    self.save(t, "Dismissed by user")
                elif op == "retry":
                    if t["status"] not in {"failed", "question"}:
                        raise ValueError("Only failed tasks or tasks awaiting an answer can be retried.")
                    t.update(status="queued", question="", decision_id="", repository_question=False, due=0, checkout_retries=0)
                    t.pop("agent_retries", None)
                    self.save(t, "Retry requested")
                else:
                    if op == "answer" and t["status"] != "question":
                        raise ValueError("Question has already been answered.")
                    if op == "answer" and ("version" in d or "choice" in d) and d.get("version") != t["version"]:
                        raise ValueError("The question changed. Open the latest question before answering.")
                    choice = None
                    if op == "answer" and "choice" in d:
                        index = d["choice"]
                        choices = t.get("answer_choices", [])
                        if type(index) is not int or not 0 <= index < len(choices):
                            raise ValueError("Choose one of the displayed answers.")
                        choice = choices[index]
                        text = choice.get("value", choice["label"])
                    else:
                        text = required(d, "text")
                    if t["status"] in TERMINAL:
                        raise ValueError("This task is closed.")
                    if op == "answer" and t.get("calendar_question"):
                        if text not in self.calendars():
                            raise ValueError("Enter the email address of a connected Calendar account.")
                        t["feedback"].append(dict(question=t["question"], answer=text))
                        t.update(
                            status="queued",
                            calendar_choice=text,
                            calendar_question=False,
                            question="",
                            due=0,
                            new_feedback="",
                        )
                        self.save(t, "Selected Google Calendar account")
                        self.wake.set()
                        return
                    if op == "answer" and t.get("repository_question"):
                        conn = self.step_source(t)
                        paths = self.repository_candidates(t, conn)
                        if d.get("picked_folder") is True and text not in paths:
                            if d.get("version") != t["version"] or not Path(text).is_absolute():
                                raise ValueError("Open the current question and choose a local checkout.")
                            text = str(worker.check_checkout_root(text))
                            if Path(text).is_relative_to(self.db.root.resolve()):
                                raise ValueError("Choose a checkout outside the app’s private storage.")
                            if self.scope.task(t) or self.scope.checkout(text):
                                raise ValueError("This checkout is ignored.")
                            if conn and conn["kind"] == "github" and bugflow.checkout_repository(text) != conn["config"]["repository"].casefold():
                                raise ValueError("Choose a checkout of " + conn["config"]["repository"] + ".")
                            project = self.db.get("projects", t["project"])
                            self.add_project(project["name"], [*project["repos"], text])
                        elif text not in paths:
                            raise ValueError("Choose a matching local checkout for this task.")
                        t["feedback"].append({"question": t["question"], "answer": text})
                        t.update(
                            status="queued",
                            repository=text,
                            repository_question=False,
                            decision_id="",
                            draft="",
                            question="",
                            due=0,
                        )
                        self.save(t, "Selected repository: " + text)
                        self.wake.set()
                        return
                    entry = {"question": t["question"], "answer": text} if op == "answer" else {"text": text}
                    if choice:
                        entry["detail"] = choice["detail"]
                    t["feedback"].append(entry)
                    t.update(status="queued", draft="", question="", due=0, new_feedback="" if choice else text)
                    self.save(t, text)
                    if op == "answer" and t.get("decision_id"):
                        for dependent in self.db.rows("tasks"):
                            if (
                                dependent["status"] == "question"
                                and dependent.get("decision_id") == t["decision_id"]
                            ):
                                dependent["feedback"].append(entry)
                                dependent.update(status="queued", question="", due=0)
                                self.save(dependent, "Shared answer: " + text)
            else:
                raise ValueError("Unknown action")
        self.wake.set()

    def gh_login(self):
        """The signed-in GitHub CLI account, if any, checked at most every two minutes."""
        at, login = self.gh_login_cache
        if time.monotonic() - at > 120:
            try:
                login = github_signin.active_login()
            except Exception:
                login = None
            self.gh_login_cache = (time.monotonic(), login)
        return login

    def setup_state(self, refresh=False):
        """Each setup step and whether it is ready, worked out from the workspace on every call."""
        inventory = self.agents.inventory(refresh)
        harness = {h["id"]: h for h in inventory["harnesses"]}
        settings = self.settings()
        provider = settings.get("provider")
        local = self.db.get("local", "setup") or {}
        connections = [c for c in self.db.rows("connections")
                       if not c.get("github_parent") and not c.get("slack_parent")]
        projects = [p for p in self.db.rows("projects") if p.get("repos")]
        agent = harness.get(provider, {})
        hermes = harness.get("hermes", {})
        # Who owns what, so the guide can say what runs where.
        owned = {}
        for record in self.db.rows("owners"):
            owned.setdefault(record["owner"], []).append(record["id"])
        peers = []
        for p in self.instances.peers():
            if p.get("trusted"):
                capacity = (p.get("info") or {}).get("capacity") or {}
                theirs = owned.get(p["id"], [])
                peers.append(dict(
                    name=p["name"], platform=p.get("platform", ""), address=p.get("address", ""),
                    state=p.get("state", ""), error=p.get("error", ""), last_seen=p.get("last_seen"),
                    online=bool(p.get("last_seen")) and self.now() - p["last_seen"] <= instances.FRESH_SECONDS,
                    agent_ready=bool(capacity.get("task_ready")), hermes_ready=bool(capacity.get("hermes_ready")),
                    slots=capacity.get("slots", 0), free=capacity.get("free", 0),
                    connections=sum(1 for i in theirs if i.startswith("connection:")),
                    tasks=sum(1 for i in theirs if i.startswith("task:")),
                    orchestrator=instances.ORCHESTRATOR in theirs))
        all_connections = self.db.rows("connections")
        placement = dict(
            here=sum(1 for c in all_connections if self.owns("connection:" + c["id"])),
            total=len(all_connections),
            # GitHub CLI and browser-profile logins live on this machine and don't sync.
            local_only=sum(1 for c in all_connections if not self.movable_connection(c)),
            orchestrator_here=self.owns(instances.ORCHESTRATOR),
        )
        steps = [
            dict(id="agent", required=True, ready=agent.get("state") == "ready", state=agent.get("state", "unknown"),
                 agent=provider, label=agent.get("label", provider), sign_in=agent.get("sign_in", ""),
                 detail=agent.get("detail", "")),
            dict(id="accounts", required=True, ready=bool(connections), count=len(connections),
                 github_login=None if any(c["kind"] == "github" for c in connections) else self.gh_login()),
            dict(id="projects", required=False, ready=bool(projects), count=len(projects),
                 skipped="projects" in local.get("skipped", [])),
            dict(id="orchestrator", required=False, ready=hermes.get("state") == "ready",
                 state=hermes.get("state", "unknown"), sign_in=hermes.get("sign_in", "")),
            dict(id="server", required=False, ready=bool(peers), peers=peers, placement=placement),
            dict(id="delivery", required=False, ready=True, dry=settings.get("dry", True)),
        ]
        missing = [s["id"] for s in steps if s["required"] and not s["ready"]]
        return dict(
            steps=steps,
            missing=missing,
            dismissed=bool(missing) and set(missing) <= set(local.get("dismissed", [])),
            first_run=not connections and not projects and not self.db.rows("tasks"),
            vps=self.vps.state(),
        )

    def remember_server(self, host):
        """The SSH host of the last server set up, so its agent sign-in works after a restart."""
        with self.db.atomic():
            local = self.db.get("local", "setup") or {"id": "setup", "dismissed": [], "skipped": []}
            local["server_host"] = host
            self.db.put("local", local)

    def setup_action(self, op, d):
        if op == "setup_connect_github":
            # Reuses the GitHub CLI's existing login. Connecting only reads, like any connection.
            login = github_signin.active_login()
            self.connect_github_account(login, None)
            self.wake.set()
            return {"account": login}
        if op in {"setup_dismiss", "setup_skip"}:
            with self.db.atomic():
                local = self.db.get("local", "setup") or {"id": "setup", "dismissed": [], "skipped": []}
                if op == "setup_dismiss":
                    local["dismissed"] = self.setup_state()["missing"]
                else:
                    step = required(d, "step", 20)
                    if step != "projects":
                        raise ValueError("Only the projects step can be skipped.")
                    local["skipped"] = sorted(set(local.get("skipped", [])) | {step})
                self.db.put("local", local)
            return
        if op == "vps_setup":
            self.vps.start(required(d, "host", 255))
            return
        if op == "vps_password":
            self.vps.password(d.get("password"))
            return
        if op == "vps_cancel":
            self.vps.cancel()
            return
        if op == "vps_sign_in":
            host = (self.db.get("local", "setup") or {}).get("server_host")
            if not host:
                address = next((p.get("address") for p in self.instances.peers() if p.get("trusted")), "")
                host = self.vps.host_for(address) if address else None
                if host:
                    self.remember_server(host)
            return self.vps.sign_in(host)
        raise ValueError("Unknown action")

    def instance_action(self, op, d):
        sync = self.instances
        if op == "instance_code":
            return {"code": sync.create_code(), "expires_in": instances.CODE_TTL}
        if op == "instance_join":
            info = sync.join(required(d, "address", 300), required(d, "code", 80))
            self.db.log("Instance paired", f"{info['name']} ({info['platform']}). Confirm on {info['name']}.")
            return {"peer": info["name"]}
        if op == "instance_remove":
            sync.remove(required(d, "peer", 64))
            self.db.log("Instance removed", "Workspace key rotated. Reconnect each account to revoke old logins.")
            return
        if op == "instance_checkout":
            path = os.path.expanduser(required(d, "path", 1000))
            origin = worker.checkout_origin(path) if os.path.isdir(path) else ""
            if not origin:
                raise ValueError("Enter the path of a Git checkout that has an origin remote.")
            with self.db.atomic():
                local = self.db.get("local", "checkouts") or {"id": "checkouts", "paths": []}
                local["paths"] = list(dict.fromkeys(local["paths"] + [os.path.realpath(path)]))
                self.db.put("local", local)
            self.origin_cache.clear()
            return {"origin": origin}
        if op == "instance_rename":
            sync.rename(required(d, "name", 60))
            return
        if op == "instance_dismiss_reconnect":
            with self.db.atomic():
                c = self.db.get("connections", required(d, "connection", 200))
                if c:
                    c.pop("reconnect_to_revoke", None)
                    self.db.put("connections", c)
            return
        if op == "move":
            to = required(d, "to", 64)
            items = d.get("items")
            if items == "connections":
                items = ["connection:" + c["id"] for c in self.db.rows("connections") if self.movable_connection(c)]
            elif items == "orchestrator":
                items = [instances.ORCHESTRATOR]
            elif not isinstance(items, list) or not all(isinstance(i, str) for i in items):
                raise ValueError("Choose what to move.")
            info = next((p["capacity"] for p in self.fresh_peers() if p["id"] == to), None)
            for item in items:
                t = self.db.get("tasks", item[5:]) if item.startswith("task:") else None
                if not t or not t.get("repository"):
                    continue
                origin = self.required_origin(t)
                if to == self.instances.id and origin and not self.find_checkout(origin):
                    raise ValueError(f"Add a checkout of {origin} here first, in Settings → Instances.")
                if to != self.instances.id and info is not None and not self.can_take(t, info):
                    raise ValueError(f"That instance has no checkout of {origin or 'this repository'}.")
            results = {item: sync.request_move(item, to) for item in items}
            sync.wake.set()
            return {"requested": sum(1 for r in results.values() if r == "requested")}
        raise ValueError("Unknown action")

    def connect_github_account(self, login, previous):
        with self.dispatch_lock, self.db.atomic():
            if previous:
                saved = self.db.get("connections", previous["id"])
                if not saved or saved.get("revision") != previous.get("revision"):
                    raise ValueError("Connection changed during sign-in. Start again.")
                if saved.get("identity", "").casefold() != login.casefold():
                    raise ValueError("Sign in to the original GitHub account.")
            ident = digest("github.com account", login.casefold())
            account = dict(
                id=ident,
                label="@" + login,
                identity=login,
                auth_method="gh_account",
                kind="github",
                config={},
                status="Discovering repositories",
                next=0,
                revision=uid(),
            )
            self.db.put("connections", account)
            self.db.log("GitHub account connected", account["label"], connection=ident)

    def sync_github_inventory(self, account):
        with self.event_locks[int(account["id"][:4], 16) % len(self.event_locks)]:
            repos = connectors.github_repositories(account)
            with self.db.atomic():
                current = self.db.get("connections", account["id"])
                if not current or current["revision"] != account["revision"]:
                    return
                sources = self.db.rows("connections")
                known = {
                    c["config"]["repository"].casefold(): c
                    for c in sources
                    if c.get("auth_method") in {"github_cli", "gh_repository"}
                    and c.get("identity", "").casefold() == account["identity"].casefold()
                }
                known.update(
                    {
                        c["config"]["repository"].casefold(): c
                        for c in sources
                        if c.get("github_parent") == account["id"]
                    }
                )
                for repo in repos:
                    child = known.pop(repo.casefold(), None)
                    if child is None:
                        child = dict(
                            id=digest(account["id"], repo.casefold()),
                            label=repo,
                            kind="github",
                            config={"repository": repo},
                            status="Connecting",
                            next=0,
                            revision=uid(),
                        )
                    if child.pop("unavailable", False) or not child.get("github_parent"):
                        child.update(next=0, revision=uid())
                    child.update(
                        github_parent=account["id"],
                        identity=account["identity"],
                        interval=max(60, 5 * len(repos)),
                    )
                    self.db.put("connections", child)
                for child in known.values():
                    if child.get("github_parent") and not child.get("unavailable"):
                        child.update(unavailable=True, status="Repository access removed", revision=uid())
                        self.db.put("connections", child)
                self.db.log("Repository sync", f"{len(repos)} repositories", connection=account["id"])
                current.update(status="Connected", next=self.now() + 900)
                self.db.put("connections", current)
            self.backfill_open_issues(account)
            self.wake.set()

    def backfill_open_issues(self, account):
        """Record open issues the account wrote or is assigned to that polling never saw.

        Polling reads only recent activity, so an issue untouched since before its repository
        was connected would otherwise never surface. See docs/specs/connectors.md."""
        try:
            issues = connectors.github_own_open_issues(account)
        except Exception as exc:
            self.db.log("Open issues unavailable", str(exc), connection=account["id"])
            return
        sources = {
            c["config"]["repository"].casefold(): c
            for c in self.db.rows("connections")
            if c.get("github_parent") == account["id"] and not c.get("unavailable")
        }
        added = 0
        for raw in issues:
            source = sources.get(raw["context"]["repository"].casefold())
            if source and not self.db.get("events", digest(source["id"], raw["key"])):
                self.ingest(source, raw)
                added += 1
        if added:
            self.db.log("Open issues recorded", f"{added} open issues", connection=account["id"])

    def release_shared_credential(self, conn):
        key = conn["credential_key"]
        if not any(
            c["id"] != conn["id"] and c.get("credential_key") == key for c in self.db.rows("connections")
        ):
            connectors.delete_secret(key)

    def connect(self, d, verified=None):
        kind = required(d, "kind", 20)
        label = required(d, "label", 180)
        config = connectors.validate(kind, d.get("config", {}))
        with self.dispatch_lock, self.db.atomic():
            previous = self.db.get("connections", d.get("id", ""))
            if previous and (
                previous.get("slack_parent") or previous.get("auth_method") == "slack_workspace"
            ):
                raise ValueError("Manage channels through the Slack workspace.")
            if previous and (previous.get("github_parent") or previous.get("auth_method") == "gh_account"):
                raise ValueError("Use GitHub CLI sign-in to reconnect this account.")
            if d.get("id") and not previous:
                raise ValueError("This connection was removed.")
            if previous and (previous["kind"] != kind or previous["config"] != config):
                raise ValueError("Create a separate connection for a different source.")
            c = previous or dict(id=uid(), kind=kind, config=config)
            secret = d.get("secret", "")
            shared = (verified or {}).get("credential_key")
            profile = (verified or {}).get("workspace_profile")
            if not previous and not secret and not shared and not profile:
                raise ValueError("A credential is required.")
            if shared or profile:
                c.update(verified)
            elif secret:
                if not verified and (
                    not isinstance(secret, str) or secret.startswith(connectors.OAUTH_PREFIX)
                ):
                    raise ValueError("Enter a manual credential or use browser sign-in.")
                connectors.store_secret(c["id"], secret)
                if c.get("credential_key"):
                    self.release_shared_credential(c)
                    del c["credential_key"]
                old_profile = c.pop("workspace_profile", None)
                if old_profile and not verified:
                    workspace_bridge.forget(self.db.root, old_profile)
                if verified:
                    c.update(verified)
                else:
                    c.pop("auth_method", None)
                    c.pop("identity", None)
                    c.pop("google_services", None)
            c.update(label=label, status="Connecting", next=0, revision=uid())
            self.db.put("connections", c)
            self.db.log("Connection configured", label, connection=c["id"])
