"""Task orchestration. Only the executor may dispatch an external write."""

import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
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
import github_signin
import connectors
import workspace_bridge
import google_workspace
from signin import SignIns
from suggestions import Suggestions
import worker
from store import Store, uid


class SyncedSecrets:
    """The app's own credential store, as seen by sync. Logins held by other tools are not listed."""

    @staticmethod
    def identities(db):
        found = []
        for c in db.rows("connections"):
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
TERMINAL = {"dismissed", "skipped", "done"}
# An owner silent this long is taken over automatically. See "Automatic takeover" in instances-and-sync.md.
TAKEOVER_SECONDS = 900
BUSY = {"sending", "uncertain"}


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
        self.now = clock
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.jobs = {}
        self.event_locks = [threading.RLock() for _ in range(32)]
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="task")
        self.sync_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="source")
        self.dispatch_lock = threading.RLock()
        self.absence = None
        self.recent_offers = {}
        self.origin_cache = {}
        self.next_balance = 0
        # The app MCP server. The HTTP service sets mcp_url once it listens.
        self.mcp_sessions = app_mcp.Sessions()
        self.mcp_tools = app_mcp.Tools(self)
        self.mcp_url = None
        self.agents = agents.Agents(root, self.settings)
        self.orchestrator = orchestrator.Orchestrator(self)
        # Guided setup and VPS setup over SSH. See docs/specs/guided-setup.md.
        self.vps = vps_setup.VpsSetup(self.instances, self.settings, self.db.log,
                                      Path(__file__).resolve().parent, open_terminal=agents.open_in_terminal)
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
            for e in self.db.rows("events"):
                if e["state"] == "working":
                    e["state"] = "new"
                    self.db.put("events", e)
        self.write_markdown()

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
            path = Path(repo).expanduser()
            if not path.is_absolute() or not (path / ".git").exists():
                raise ValueError(f"Choose a Git working tree for {repo}.")
            paths.append(str(path.resolve()))
        self.db.put("projects", {**previous, "repos": list(dict.fromkeys(paths))})

    def repositories(self, project):
        projects = self.db.rows("projects")
        parents = {p["name"]: p.get("parent") for p in projects}
        return list(
            dict.fromkeys(
                path
                for p in projects
                if project == self.global_project
                or p["name"] == project
                or project in self.ancestors(p["name"], parents)
                for path in p["repos"]
            )
        )

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
        self.save(task, "Task created")
        return task

    def save(self, t, message, connection=""):
        t["version"] += 1
        self.db.put("tasks", t)
        self.db.log(t["status"], message, t["project"], t["id"], connection)

    def rule(self, project, kind):
        row = self.db.get("rules", digest(project, kind))
        return row["mode"] if row else "review"

    def auto(self, t):
        kinds = {t["kind"], *t.get("categories", [])}
        if t["commitments"]:
            kinds.add("commitment")
        if t.get("takeover_version") == t["version"]:
            return False  # a version prepared by an instance that went silent is sent only after approval
        return not self.settings()["dry"] and all(self.rule(t["project"], k) == "auto" for k in kinds)

    def infer(self, role, payload, project, task=""):
        with self.db.lock:
            revisions = [s["id"] for s in self.db.rows("skills") if s["active"]]
            instructions = {
                kind: (self.db.root / "skills" / (kind + ".md")).read_text() for kind in ("style", "workflow")
            }
            rules = [r for r in self.db.rows("rules") if r["project"] == project]
        provider = self.settings()["provider"]
        self.db.log("Agent started", f"{role} · {provider} · skills {', '.join(revisions)}", project, task)
        context = {**payload, "instructions": instructions, "project": project, "rules": rules}
        if role == "task" and task and self.mcp_url:
            token = self.mcp_sessions.issue(app_mcp.TASK, task, self.task_actions(task))
            try:
                answer = worker.run_agent(role, context, provider, mcp=dict(url=self.mcp_url, token=token))
            finally:
                self.mcp_sessions.revoke(token)
        else:
            answer = worker.run_agent(role, context, provider)
        self.db.log("Agent result", json.dumps(answer, ensure_ascii=False), project, task)
        return answer

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
            paths += p.get("repos", [])
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
        target = self.db.root / "checkouts" / re.sub(r"[^\w.-]+", "_", origin).strip("_")
        path = str(worker.clone_checkout(remote, target))
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
            fields["worktree"] = str(worker.import_worktree(path, self.db.root / "worktrees", key, package))
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

    def record_origins(self):
        """Note each owned repository task's origin, so a peer can take it over if this instance goes silent."""
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
        if self.suggestions.next_scan <= now:
            submit("activity-suggestions", self.suggestions.run, None, self.pool)
        owned = set()
        for c in self.db.rows("connections"):
            if not self.owns("connection:" + c["id"]):
                continue
            owned.add(c["id"])
            if not c.get("unavailable") and c.get("next", 0) <= now:
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
        for t in sorted(self.db.rows("tasks"), key=lambda t: t.get("created", 0)):
            if not self.owns("task:" + t["id"]):
                continue
            if t["status"] in {"queued", "scheduled"} and t.get("due", 0) <= now:
                # At most the slot count at once; nothing new while preparing for sleep or stop.
                if "task:" + t["id"] not in self.jobs and not absent and running < slots:
                    submit("task:" + t["id"], self.process, t["id"], self.pool)
                    running += 1
            if t["status"] == "review" and t.get("approved_version") == t["version"]:
                submit("send:" + t["id"], self.approved_send, (t["id"], t["version"]), self.sync_pool)
            elif t["status"] == "review" and self.auto(t):
                submit("send:" + t["id"], self.autosend, (t["id"], t["version"]), self.sync_pool)
        for e in self.db.rows("events"):
            if e.get("connection") and e["connection"] not in owned:
                continue
            if e["state"] == "new" or (e["state"] == "ignored" and e.get("next", 0) <= now):
                submit("discover:" + e["id"], self.discover, e["id"], self.pool)

    def ingest(self, conn, raw):
        with self.db.atomic():
            current = self.db.get("connections", conn["id"])
            if not current or current.get("unavailable") or current.get("revision") != conn.get("revision"):
                return
            key = digest(conn["id"], raw["key"])
            prev = self.db.get("events", key)
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
                    and any(bugflow.checkout_repository(path) == repo for path in p["repos"])
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
            with self.db.atomic():
                if self.db.get("events", key)["generation"] != e["generation"]:
                    return
                c = self.db.get("connections", e["connection"])
                if not c:
                    return
                t = self.db.get("tasks", key)
                if t:
                    e["project"] = t["project"]
                elif not e["project"]:
                    suggested = out.get("project")
                    e["project"] = suggested if suggested in project_names else self.global_project
                if not t and (out.get("relevant") or out.get("owned")):
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
            (x["raw"] for x in self.db.rows("events") if x["id"] != e["id"] and self.same_thread(e, x)),
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
            t["status"] = "working"
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
                self.save(latest, latest.get("question") or latest.get("reason") or latest["status"])
        except Exception as exc:
            with self.db.atomic():
                latest = self.db.get("tasks", key)
                if latest["version"] == version and self.owns("task:" + key):
                    latest.update(status="failed", result=str(exc))
                    self.save(latest, str(exc))
        self.wake.set()

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
            decision_kind=category,
            decision_id=digest(t["project"], " ".join(question.casefold().split())),
            reason="Waiting for your answer",
        )

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
        if coding:
            if location and not repository:
                # Older tasks stored only the verified task worktree.
                location = str(worker.resume_worktree(location, self.db.root / "worktrees", t["id"]))
            elif location:
                location = str(worker.prepare_worktree(repository, self.db.root / "worktrees", t["id"]))
            else:
                candidates = self.repository_candidates(t, conn)
                if not repository:
                    repository = self.match_repository(t, conn, candidates)
                elif repository not in candidates:
                    repository = ""
                if not repository:
                    question = (
                        "Which repository should this task use?"
                        if candidates
                        else "Link the target checkout in Projects, then select it for this task."
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
                        "decision_id": digest(t["id"], "repository"),
                    }
                location = str(worker.prepare_worktree(repository, self.db.root / "worktrees", t["id"]))
            files = worker.worktree_context(location)
        context = dict(
            task=t,
            event=e["raw"] if e else {},
            files=files,
            google_calendars=list(self.calendars()),
            now=self.now(),
            thread=self.thread_context(e) if e else [],
            children=[
                dict(title=x["title"], result=x["result"], status=x["status"])
                for x in self.db.rows("tasks")
                if x.get("parent") == t["id"]
            ],
        )
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
                and x["id"] != e["id"]
                and x["raw"].get("url")
                and x["raw"]["url"] in body
            ][:12]
            for source in self.db.rows("connections"):
                if (
                    source["kind"] != "github"
                    or not source["config"].get("repository")
                    or source.get("unavailable")
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
        if bug and t.get("changes") and t.get("milestones"):
            out = dict(outcome="done", text=t["result"], kind="bug")
        else:
            out = self.infer("task", context, t["project"], t["id"])
        result = out["text"]
        update = dict(
            result=result,
            worktree=location,
            repository=repository,
            repository_question=False,
            new_feedback="",
            question="",
            draft="",
            commitments=[],
            due=0,
        )
        outcome = out["outcome"]
        newkind = out.get("kind") or t["kind"]
        if not coding and newkind in {"coding", "bug", "bugfix"}:
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
                self.skill("style", out["style"], False, "Task agent suggestion")
        if outcome == "question":
            with self.db.atomic():
                for step in out.get("independent_steps", [])[:6]:
                    title = required(step, "title", 300)
                    ident = digest(t["id"], title)
                    if not self.db.get("tasks", ident) and t.get("depth", 0) < 2:
                        self.new_task(
                            title,
                            t["project"],
                            required(step, "body"),
                            id=ident,
                            parent=t["id"],
                            depth=t.get("depth", 0) + 1,
                        )
            return {**update, **self.decision(t, result)}
        if out.get("files"):
            if not location:
                return {
                    **update,
                    **self.decision(t, "Which local Git repository should be linked to this project?"),
                }
            with self.db.atomic():
                if self.db.get("tasks", t["id"])["version"] != t["version"]:
                    raise ValueError("Task changed during preparation.")
                paths = worker.apply_worktree_files(location, out["files"], files)
                self.db.log("Files edited", "\n".join(paths), t["project"], t["id"])
            update["changes"] = out["files"]
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

    def repository_candidates(self, t, conn):
        paths = self.repositories(t["project"])
        if conn and conn["kind"] == "github":
            slug = conn["config"]["repository"].casefold()
            return [p for p in paths if bugflow.checkout_repository(p) == slug]
        return paths

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
        events = [e for e in self.db.rows("events") if start <= e["raw"]["timestamp"] <= end]
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
        if op.startswith("setup_") or op.startswith("vps_"):
            return self.setup_action(op, d)
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
        with self.dispatch_lock, self.db.atomic():
            if op in {"self_check", "app_finding"}:
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
            elif op in {"feedback", "answer", "retry", "dismiss", "reconcile"}:
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
                elif op == "dismiss":
                    t.update(status="dismissed", draft="", question="")
                    self.save(t, "Dismissed by user")
                elif op == "retry":
                    if t["status"] != "failed":
                        raise ValueError("Only failed tasks can be retried.")
                    t.update(status="queued", due=0)
                    self.save(t, "Retry requested")
                else:
                    text = required(d, "text")
                    if op == "answer" and t["status"] != "question":
                        raise ValueError("Question has already been answered.")
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
                        event = self.db.get("events", t.get("event", ""))
                        conn = self.db.get("connections", event["connection"]) if event else None
                        paths = self.repository_candidates(t, conn)
                        if text not in paths:
                            raise ValueError("Choose a registered checkout for this task's repository.")
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
                    t["feedback"].append(entry)
                    t.update(status="queued", draft="", question="", due=0, new_feedback=text)
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
        peers = []
        for p in self.instances.peers():
            if p.get("trusted"):
                capacity = (p.get("info") or {}).get("capacity") or {}
                peers.append(dict(name=p["name"], platform=p.get("platform", ""), address=p.get("address", ""),
                                  agent_ready=bool(capacity.get("task_ready"))))
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
            dict(id="server", required=False, ready=bool(peers), peers=peers),
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
            return self.vps.sign_in()
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
            self.wake.set()

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
