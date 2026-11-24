"""Resolve workspace exclusions against task, source and checkout identities."""

from pathlib import Path
import re

import bugflow
import selfcheck


def normalize(data, projects):
    def entries(key):
        items = data.get(key)
        if not isinstance(items, list) or not all(isinstance(v, str) and v.strip() for v in items):
            raise ValueError("Choose project names and repository names or absolute paths.")
        return {v.strip() for v in items}

    names = entries("projects")
    if names - {p["name"] for p in projects if not p.get("global")}:
        raise ValueError("Choose an existing named project.")
    repos = set()
    for value in entries("repositories"):
        if Path(value).is_absolute():
            repos.add(str(Path(value).resolve()))
        else:
            slug = bugflow._github_repository(value) or value.casefold()
            if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug, re.ASCII) or {".", ".."}.intersection(slug.split("/")):
                raise ValueError("Use a GitHub repository or an absolute checkout path.")
            repos.add(slug)
    return {"id": "ignorelist", "projects": sorted(names), "repositories": sorted(repos)}


class WorkScope:
    def __init__(self, db):
        self.db = db
        self.config = db.get("config", "ignorelist") or {"projects": [], "repositories": []}
        self.projects = set(self.config["projects"])
        repos = self.config["repositories"]
        self.paths = {Path(r) for r in repos if Path(r).is_absolute()}
        self.slugs = {r.casefold() for r in repos if not Path(r).is_absolute()}
        self.active = bool(self.projects or repos)
        if not self.active:
            return
        projects = db.rows("projects")
        parents = {p["name"]: p.get("parent") for p in projects}
        for name in parents:
            current = name
            while current and current not in self.projects:
                current = parents.get(current)
            if current:
                self.projects.add(name)
        registered = {Path(path) for p in projects for path in p["repos"]}
        self.paths.update(Path(path) for p in projects if p["name"] in self.projects for path in p["repos"])
        origins = {p: bugflow.checkout_repository(str(p)) for p in registered | self.paths}
        self.slugs.update(origins[p] for p in self.paths if origins[p])
        self.paths.update(p for p in registered if origins[p] in self.slugs)
        identities = self.slugs | {str(p) for p in self.paths}
        self.references = re.compile(r"(?<![\w.-])(?:" + "|".join(map(re.escape, identities)) +
                                     r")(?:\.git)?(?=$|[^\w.-]|\.(?:\s|$))", re.I) if identities else None

    def path(self, value):
        if not value or not self.paths:
            return False
        path = Path(value)
        return any(path == p or p in path.parents for p in self.paths)

    def connection(self, conn):
        return bool(conn and conn.get("config", {}).get("repository", "").casefold() in self.slugs)

    def checkout(self, path):
        return self.path(path) or bool(path and self.slugs and bugflow.checkout_repository(path) in self.slugs)

    def matches(self, row, *, check_text=True):
        if not self.active or not row:
            return False
        raw = row.get("raw", row)
        repo = raw.get("context", {}).get("repository") or row.get("repository") or ""
        if row.get("source") == "File" and row.get("folder") and self.path(Path(row["folder"]) / row["title"]):
            return True
        if (row.get("project") in self.projects or repo.casefold() in self.slugs or self.path(repo)
                or (row.get("github_repository") or "").casefold() in self.slugs
                or self.path(row.get("folder")) or row.get("milestones", {}).get("repository", "").casefold() in self.slugs):
            return True
        if check_text and not repo and self.references and any(
            self.references.search(raw.get(k) or "") for k in ("title", "body", "url", "destination", "detail")
        ):
            return True
        return any(self.connection(self.db.get("connections", row[k])) for k in ("connection", "github") if row.get(k))

    def task(self, task):
        if not self.active:
            return False
        seen = set()
        while task:
            if (task.get("special") in {"app_finding", "self_check"}
                    and selfcheck.REPOSITORY.casefold() in self.slugs):
                return True
            event = self.db.get("events", task["event"]) if task.get("event") else None
            source_repo = event and event["raw"].get("context", {}).get("repository")
            if self.matches(task, check_text=not source_repo) or self.matches(event):
                return True
            if any(self.matches(row) for row in task.get("evidence", [])):
                return True
            if task.get("worktree") and not task.get("repository") and self.checkout(task["worktree"]):
                return True
            parent = task.get("parent")
            if not parent or parent in seen:
                break
            seen.add(parent)
            task = self.db.get("tasks", parent)
        return False
