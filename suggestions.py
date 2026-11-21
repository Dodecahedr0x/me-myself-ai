"""Evidence-based suggestions from connected sources and explicitly selected Mac activity."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading

import desktop_chats
from store import uid

WINDOW = 3 * 3600
INTERVAL = 300
SKIP = {"node_modules", "target", "build", "dist", "__pycache__", "vendor", "DerivedData", "Library"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def foreground_app():
    script = (
        'ObjC.import("AppKit"); ObjC.unwrap($.NSWorkspace.sharedWorkspace.frontmostApplication.localizedName)'
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", "-e", script], capture_output=True, timeout=5, check=True
    )
    return result.stdout.decode().strip()[:160]


def recent_commits(root, start, end):
    if not (root / ".git").exists():
        return []

    def git(*args):
        return subprocess.run(
            ["/usr/bin/git", "--no-pager", "-C", str(root), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=8,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )

    email = git("config", "--get", "user.email").stdout.strip()
    if not email or git("rev-parse", "--verify", "--quiet", "HEAD").returncode:
        return []
    result = git(
        "log",
        "HEAD",
        "--no-show-signature",
        "--no-notes",
        "--max-count=100",
        f"--since=@{int(start) - 1}",
        "--format=%H%x00%ct%x00%ae%x00%s",
    )
    if result.returncode:
        raise ValueError("Could not read recent Git history.")
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(b"\0", 3)
        if len(fields) != 4 or fields[2].lower() != email.lower() or not fields[1].isdigit():
            continue
        sha, when, _, subject = fields
        at = int(when)
        if start <= at <= end:
            rows.append(
                dict(
                    id=fingerprint(["commit", str(root), sha.decode("ascii")]),
                    source="Git",
                    title=subject.decode("utf-8", "replace")[:300],
                    detail=f"{root.name} · {sha[:10].decode('ascii')}",
                    folder=str(root),
                    at=at,
                )
            )
    return rows[:16]


def changed_files(folder, start, end, workspace):
    root = Path(folder)
    if root.is_symlink() or root.resolve() != root or not root.is_dir():
        raise ValueError("Folder is unavailable.")
    entries = []
    visited = 0
    partial = False

    def inaccessible(_):
        nonlocal partial
        partial = True

    for base, dirs, files in os.walk(root, onerror=inaccessible):
        directory = Path(base)
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in SKIP
            and not (directory / d).is_symlink()
            and directory / d != workspace
        )
        visited += len(dirs)
        remaining = max(0, 25000 - visited)
        for name in sorted(files)[:remaining]:
            if name.startswith("."):
                continue
            path = directory / name
            try:
                info = path.lstat()
            except OSError:
                partial = True
                continue
            if stat.S_ISREG(info.st_mode) and start <= info.st_mtime <= end:
                relative = str(path.relative_to(root))
                entries.append(
                    dict(
                        id=fingerprint(["file", str(path), info.st_mtime_ns]),
                        source="File",
                        title=relative,
                        detail="Modified in " + folder,
                        at=info.st_mtime,
                        folder=folder,
                    )
                )
        visited += len(files)
        if visited >= 25000:
            partial = True
            break
    return sorted(entries, key=lambda r: (r["at"], r["id"]), reverse=True)[:20], partial


class Suggestions:
    def __init__(self, engine):
        self.engine = engine
        self.db = engine.db
        self.next_scan = 0
        self.lock = threading.Lock()

    def config(self):
        return self.db.get("config", "local_activity") or dict(
            id="local_activity", folders=[], foreground=False, revision=""
        )

    def state(self):
        return dict(
            config=self.config(),
            scan=self.db.get("config", "suggestion_scan") or {},
            pending=[s for s in self.db.rows("suggestions") if s["status"] == "pending"],
        )

    def configure(self, data):
        folders, focus = data.get("folders"), data.get("foreground")
        if not isinstance(folders, list) or type(focus) is not bool:
            raise ValueError("Choose folders and an app activity setting.")
        paths = []
        for value in folders:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Choose a folder.")
            folder = Path(value).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("Choose an existing absolute folder.")
            folder = folder.resolve()
            workspace = self.db.root.resolve()
            if folder == workspace or workspace in folder.parents:
                raise ValueError("Choose a work folder outside the agent's data directory.")
            paths.append(str(folder))
        paths = list(dict.fromkeys(paths))
        with self.db.atomic():
            self.db.put("config", dict(self.config(), folders=paths, foreground=focus, revision=uid()))
            self.db.log(
                "Local activity configured", f"{len(paths)} watched folders", connection="desktop:mac"
            )
            if not focus:
                for observation in self.db.rows("app_observations"):
                    self.db.delete("app_observations", observation["id"])
            self.next_scan = 0
            self.engine.wake.set()

    def configure_chat(self, data):
        provider = data.get("provider")
        enabled = data.get("enabled")
        folder = data.get("folder", "")
        if (
            not isinstance(provider, str)
            or provider not in desktop_chats.NAMES
            or type(enabled) is not bool
            or not isinstance(folder, str)
        ):
            raise ValueError("Choose a supported desktop chat source.")
        if enabled and folder:
            path = Path(folder)
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("Choose the extracted chat export folder.")
            folder = str(path.resolve())
        with self.db.atomic():
            config = self.config()
            chats = config.setdefault("chats", {})
            previous = chats.get(provider)
            if enabled and previous and previous["folder"] == folder:
                return
            if enabled:
                chats[provider] = dict(folder=folder, revision=uid())
            else:
                chats.pop(provider, None)
            config["revision"] = uid()
            self.db.put("config", config)
            for item in self.db.rows("suggestions"):
                if item["status"] == "pending" and any(
                    r.get("chat_source") == provider for r in item["evidence"]
                ):
                    self.db.delete("suggestions", item["id"])
            self.db.delete("config", "suggestion_scan")
            self.db.log(
                "Chat source connected" if enabled else "Chat source disconnected",
                desktop_chats.NAMES[provider],
                connection="desktop:" + provider,
            )
            self.next_scan = 0
        self.engine.wake.set()

    def connected(self):
        return {c["id"]: c for c in self.db.rows("connections") if not c.get("unavailable")}

    def gather(self, config, now):
        start = now - WINDOW
        records = []
        gaps = []
        for provider, settings in config.get("chats", {}).items():
            try:
                entries, missing = desktop_chats.collect(provider, settings, start, now)
            except (OSError, ValueError):
                entries, missing = (
                    [],
                    [desktop_chats.NAMES[provider] + ": Could not read local chat activity."],
                )
            records.extend(entries)
            gaps.extend(missing)
            self.db.log(
                "Activity scan",
                " · ".join([f"{len(entries)} recent exchanges", *missing]),
                connection="desktop:" + provider,
            )
        chat_count, chat_gaps = len(records), len(gaps)
        for folder in config["folders"]:
            try:
                entries, partial = changed_files(folder, start, now, self.db.root.resolve())
                records.extend(entries)
                records.extend(recent_commits(Path(folder), start, now))
                if partial:
                    gaps.append(folder + ": partial scan")
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                gaps.append(folder + ": " + str(exc))
        if config["foreground"]:
            try:
                name = foreground_app()
            except (OSError, ValueError, subprocess.SubprocessError):
                name = ""
                gaps.append("Could not read the frontmost app.")
            with self.db.atomic():
                if self.config()["revision"] == config["revision"]:
                    if name and name not in {"MeMyselfAI", "loginwindow"}:
                        self.db.put(
                            "app_observations",
                            dict(
                                id=fingerprint(["app", name]),
                                source="Mac app",
                                title=name,
                                detail="Frontmost app when sampled",
                                at=now,
                            ),
                        )
                    for row in self.db.rows("app_observations"):
                        if start <= row["at"] <= now:
                            records.append(row)
                        else:
                            self.db.delete("app_observations", row["id"])
        if config["foreground"] or config["folders"]:
            self.db.log(
                "Activity scan",
                " · ".join([f"{len(records) - chat_count} recent observations", *gaps[chat_gaps:]]),
                connection="desktop:mac",
            )
        sources = self.connected()
        for e in self.db.rows("events"):
            c = sources.get(e["connection"])
            r = e["raw"]
            if not c or not start <= r["timestamp"] <= now or r.get("owned"):
                continue
            records.append(
                dict(
                    id=e["id"],
                    source=r.get("context", {}).get("google_service") or c["kind"],
                    title=r["title"][:300],
                    detail=r["body"][:600],
                    authored_by_user=bool(r.get("authored")),
                    destination=r["destination"],
                    at=r["timestamp"],
                    url=r.get("url", ""),
                    project=e["project"],
                    connection=c["id"],
                    generation=e["generation"],
                )
            )
        # Keep a busy mailbox or directory from crowding out the other sources.
        counts = {}
        recent = {}
        for row in sorted(records, key=lambda r: (r["at"], r["id"]), reverse=True):
            source = row["source"]
            if counts.get(source, 0) < 16:
                recent[row["id"]] = row
                counts[source] = counts.get(source, 0) + 1
        return recent, gaps

    def run(self, _=None):
        if not self.lock.acquire(blocking=False):
            return
        config = self.config()
        now = self.engine.now()
        self.next_scan = now + INTERVAL
        gaps = []
        try:
            evidence, gaps = self.gather(config, now)
            signature = fingerprint([(k, r.get("generation")) for k, r in sorted(evidence.items())])
            last = self.db.get("config", "suggestion_scan") or {}
            tasks = self.db.rows("tasks")
            prior = self.db.rows("suggestions")
            covered = {t["id"] for t in tasks} | {s["anchor"] for s in prior}
            if any(
                r["source"] != "Mac app" and k not in covered for k, r in evidence.items()
            ) and signature != last.get("signature"):
                projects = [p["name"] for p in self.db.rows("projects")]
                payload = dict(
                    evidence=list(evidence.values()),
                    existing_tasks=[dict(title=t["title"], status=t["status"]) for t in tasks[-100:]],
                    previous_suggestions=[s["title"] for s in prior[-80:]],
                    projects=projects,
                    global_scope=self.engine.global_project,
                    now=now,
                )
                if self.config()["revision"] != config["revision"]:
                    return
                out = self.engine.infer("suggestions", payload, self.engine.global_project)
                with self.db.atomic():
                    if self.config()["revision"] != config["revision"]:
                        return
                    self.record(out["suggestions"], evidence, projects)
            with self.db.atomic():
                if self.config()["revision"] == config["revision"]:
                    self.db.put("config", dict(id="suggestion_scan", at=now, signature=signature, gaps=gaps))
        except Exception as exc:
            with self.db.atomic():
                if self.config()["revision"] == config["revision"]:
                    self.db.put("config", dict(id="suggestion_scan", at=now, gaps=gaps + [str(exc)[:400]]))
        finally:
            self.lock.release()

    @staticmethod
    def evidence_connection(row):
        if row.get("chat_source"):
            return "desktop:" + row["chat_source"]
        return row.get("connection") or "desktop:mac"

    def record(self, proposals, evidence, projects):
        prior = self.db.rows("suggestions")
        used = {s["anchor"] for s in prior}
        titles = {s["title"].strip().casefold() for s in prior} | {
            t["title"].strip().casefold() for t in self.db.rows("tasks")
        }
        for p in proposals:
            anchor = p["anchor"]
            refs = list(dict.fromkeys([anchor, *p["evidence"]]))
            if anchor in used or self.db.get("tasks", anchor) or any(k not in evidence for k in refs):
                continue
            rows = [evidence[k] for k in refs]
            if rows[0]["source"] == "Mac app" or p["title"].strip().casefold() in titles:
                continue
            if not self.valid_sources(rows):
                continue
            project = p["project"] if p["project"] in projects else self.engine.global_project
            suggestion = dict(
                id=fingerprint(["suggestion", anchor]),
                anchor=anchor,
                title=p["title"],
                reason=p["reason"],
                body=p["body"],
                project=project,
                evidence=rows,
                status="pending",
                created=self.engine.now(),
            )
            self.db.put("suggestions", suggestion)
            self.db.log(
                "Task suggested",
                p["title"] + " · " + p["reason"],
                project,
                connection=self.evidence_connection(rows[0]),
            )
            used.add(anchor)
            titles.add(p["title"].strip().casefold())

    def valid_sources(self, rows):
        sources = self.connected()
        config = self.config()
        for r in rows:
            if r.get("chat_source") and config.get("chats", {}).get(r["chat_source"], {}).get(
                "revision"
            ) != r.get("chat_revision"):
                return False
            if "folder" in r and r["folder"] not in config["folders"]:
                return False
            if r["source"] == "Mac app" and not config["foreground"]:
                return False
            if "connection" in r:
                e = self.db.get("events", r["id"])
                if (self.db.get("tasks", r["id"]) or {}).get("status") in {"skipped", "dismissed"}:
                    return False
                if (
                    not e
                    or e["generation"] != r["generation"]
                    or e["raw"].get("owned")
                    or r["connection"] not in sources
                ):
                    return False
        return True

    def decide(self, key, start):
        with self.db.atomic():
            s = self.db.get("suggestions", key)
            if not s or s["status"] != "pending":
                raise ValueError("This suggestion has already been handled.")
            if start:
                if not self.valid_sources(s["evidence"]):
                    raise ValueError(
                        "The source activity changed. Dismiss this suggestion and review the latest activity."
                    )
                primary = s["evidence"][0]
                event = self.db.get("events", s["anchor"]) if "connection" in primary else None
                task_id = event["id"] if event else s["id"]
                if not self.db.get("tasks", task_id):
                    fields = dict(
                        id=task_id,
                        source="Activity suggestion",
                        reason=s["reason"],
                        evidence=s["evidence"],
                        source_connection=self.evidence_connection(primary),
                    )
                    if event:
                        raw = event["raw"]
                        fields.update(
                            event=event["id"],
                            source=primary["source"],
                            url=raw.get("url", ""),
                            account=raw["account"],
                            destination=raw["destination"],
                        )
                    self.engine.new_task(s["title"], s["project"], s["body"], **fields)
                s["task"] = task_id
            s["status"] = "started" if start else "dismissed"
            self.db.put("suggestions", s)
            self.db.log(
                "Suggestion " + s["status"],
                s["title"],
                s["project"],
                s.get("task", ""),
                connection=self.evidence_connection(s["evidence"][0]),
            )
        self.engine.wake.set()
