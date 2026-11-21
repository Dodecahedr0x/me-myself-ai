"""Run the desktop bug workflow with local Git and fixture provider transports."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from engine import Engine, digest
import selfcheck


class BugIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        self.repo = root / "checkout"
        self.repo.mkdir()

        def git(*args):
            return subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    *args,
                ],
                text=True,
            ).strip()

        git("init", "-q", "-b", "main")
        self.file = self.repo / "report.py"
        self.file.write_text("def total(values):\n    return values[0]\n")
        git("add", "report.py")
        git("commit", "-qm", "Initial fixture")
        # This remote is solely inspected by 'git remote get-url'. No Git fetch or push.
        git("remote", "add", "origin", "https://github.com/fixture/report.git")
        self.base = git("rev-parse", "HEAD")
        self.file.write_text("# User's outstanding changes\n")
        self.fixed = "def total(values):\n    return sum(values)\n"
        self.now = 1_800_000_000
        self.state_dir = root / "state"
        self.engine = Engine(self.state_dir, lambda: self.now)
        self.addCleanup(lambda: self.engine.close())
        self.engine.add_project("Reporting", str(self.repo))
        self.slack = dict(
            id="slack-id",
            kind="slack",
            label="Team",
            config={"channel": "C01"},
            status="Connected",
        )
        self.github = dict(
            id="github-id",
            kind="github",
            label="GitHub",
            config={"repository": "fixture/report"},
            status="Connected",
        )
        for connection in (self.slack, self.github):
            self.engine.db.put("connections", connection)
        self.ts = str(self.now - 5) + ".123456"
        self.key = digest(self.slack["id"], "T01/C01/" + self.ts)
        self.pr_url = "https://github.com/fixture/report/pull/8"
        self.pr, self.branch = {}, ""
        self.writes = []
        for name, kwargs in [
            ("engine.worker.run_agent", {"side_effect": self.model}),
            ("connectors._http", {"side_effect": self.transport}),
            ("connectors._secret", {"return_value": "test-only"}),
            ("connectors.build_opener", {"side_effect": AssertionError("Unexpected network")}),
        ]:
            mock = patch(name, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def model(self, role, payload, model):
        if role == "discovery":
            return dict(
                relevant=True,
                owned=False,
                kind="bug",
                title="Empty report fails",
                project="Reporting",
                reason="The reporter asks for a fix.",
            )
        self.assertEqual(role, "task")
        if "files_changed" in payload:
            self.assertIn("+    return sum(values)", payload["files_changed"][0]["patch"])
            return dict(outcome="draft", text="report.py now handles empty input. Add an empty-list test.")
        if "verified_message" in payload:
            return dict(outcome="draft", kind="bug", text="The fix handles empty reports.")
        self.assertIn("return values[0]", payload["files"]["report.py"])
        return dict(
            outcome="done",
            kind="bug",
            text="Handle an empty report by summing its values.",
            files=[dict(path="report.py", content=self.fixed)],
        )

    def transport(self, url, token, params=None, body=None, method=None):
        path = urlsplit(url).path
        if body is not None:
            self.writes.append((path, body))
        if path == "/api/auth.test":
            return {"ok": True, "user_id": "U2", "team_id": "T01", "url": "https://fixture.slack.com/"}, {}
        if path == "/api/conversations.history":
            return {
                "ok": True,
                "messages": [dict(ts=self.ts, user="U1", text="An empty report crashes. Please fix it.")],
            }, {}
        if path == "/api/chat.postMessage":
            self.assertEqual((body["channel"], body["thread_ts"]), ("C01", self.ts))
            self.assertIn("<@U1>", body["text"])
            self.assertIn(self.pr_url, body["text"])
            return {"ok": True, "channel": "C01", "ts": "1800000001.000100"}, {}
        if path == "/user":
            return {"login": "fixture-coder"}, {}
        repo = "/repos/fixture/report"
        self.assertTrue(path.startswith(repo))
        suffix = path[len(repo) :]
        if body is not None:
            if suffix == "/issues":
                self.assertIn("crashes", body["body"])
                return {"number": 7, "html_url": "https://github.com/fixture/report/issues/7"}, {}
            if suffix == "/git/trees":
                self.assertEqual(
                    body["tree"], [dict(type="blob", mode="100644", path="report.py", content=self.fixed)]
                )
                return {"sha": "newtree"}, {}
            if suffix == "/git/commits":
                self.assertEqual(body["parents"], [self.base])
                return {"sha": "newcommit"}, {}
            if suffix == "/git/refs":
                self.branch = body["ref"].removeprefix("refs/heads/")
                self.assertEqual(body["sha"], "newcommit")
                return {"ref": body["ref"]}, {}
            if suffix == "/pulls":
                self.assertIn("Fixes #7", body["body"])
                self.pr = dict(
                    state="open",
                    mergeable=True,
                    body=body["body"],
                    html_url=self.pr_url,
                    number=8,
                    head=dict(ref=self.branch, sha="newcommit", repo={"full_name": "fixture/report"}),
                    base=dict(ref="main", sha=self.base, repo={"full_name": "fixture/report"}),
                    changed_files=1,
                )
                return self.pr, {}
            if suffix == "/pulls/8/reviews":
                self.assertEqual(body["event"], "COMMENT")
                return dict(id=1, state="COMMENTED", commit_id=body["commit_id"], body=body["body"],
                            user={"login":"fixture-coder"},
                            html_url=self.pr_url + "#pullrequestreview-1"), {}
            self.fail("Unexpected fixture write: " + suffix)
        reads = {
            "": {"full_name": "fixture/report", "default_branch": "main"},
            "/issues/7": {"state": "open", "assignees": []},
            "/git/ref/heads/main": {"object": {"sha": self.base}},
            "/git/commits/" + self.base: {"tree": {"sha": "oldtree"}},
            "/git/ref/heads/" + self.branch: {"object": {"sha": "newcommit"}},
            "/pulls/8": self.pr,
            "/pulls/8/files": [dict(filename="report.py", status="modified", additions=1, deletions=1,
                                    patch="@@ -2 +2 @@\n-    return values[0]\n+    return sum(values)")],
        }
        return reads[suffix], {}

    def row(self):
        return self.engine.db.get("tasks", self.key)

    def prepare(self, expected):
        previous = list(self.writes)
        self.engine.process(self.key)
        task = self.row()
        self.assertEqual(task["status"], "review", task["result"])
        self.assertEqual(task["operation"], expected)
        self.assertTrue(task["draft"] and task["account"] and task["destination"])
        self.engine.autosend((self.key, task["version"]))
        self.assertEqual(self.writes, previous, "Preparing and dry-mode autosend must be read-only")
        return task

    def approve(self, task):
        self.engine.action(dict(op="approve", id=self.key, version=task["version"]))
        written = list(self.writes)
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="approve", id=self.key, version=task["version"]))
        self.assertEqual(self.writes, written, "A repeated approval duplicated a provider write")

    def run_to_notification(self):
        self.assertTrue(self.engine.settings()["dry"])
        self.engine.sync(self.slack)
        self.engine.discover(self.key)
        self.assertEqual(self.row()["kind"], "bug")
        for expected, count in [("create issue", 1), ("publish branch", 4), ("create pull request", 5)]:
            task = self.prepare(expected)
            if expected == "create issue":
                self.assertEqual((Path(task["worktree"]) / "report.py").read_text(), self.fixed)
                self.assertEqual(self.file.read_text(), "# User's outstanding changes\n")
                self.assertEqual(self.writes, [])
            self.approve(task)
            self.assertEqual(len(self.writes), count)
            self.assertEqual(self.row()["status"], "queued")
            if expected == "create pull request":
                self.engine.close()
                self.engine = Engine(self.state_dir, lambda: self.now)
                self.assertEqual(len(self.row()["receipts"]), 3)
        notification = self.prepare("reply")
        self.assertEqual(notification["account"], "T01/U2")
        self.assertIn("C01", notification["destination"])
        self.assertIn("<@U1>", notification["draft"])
        self.assertIn(self.pr_url, notification["draft"])
        return notification

    def test_four_approvals_complete_bug_and_notify_reporter(self):
        self.approve(self.run_to_notification())
        task = self.row()
        self.assertEqual(task["status"], "done")
        self.assertEqual(
            [r["operation"] for r in task["receipts"]],
            ["create issue", "publish branch", "create pull request", "reply"],
        )
        self.assertEqual(
            [r["connection"] for r in task["receipts"]], [self.github["id"]] * 3 + [self.slack["id"]]
        )
        self.assertEqual(
            [p.rsplit("/", 1)[-1] for p, _ in self.writes],
            ["issues", "trees", "commits", "refs", "pulls", "chat.postMessage"],
        )
        self.assertIn("Slack C01", task["receipts"][-1]["receipt"])

    @patch.object(selfcheck, "REPOSITORY", "fixture/report")
    def test_app_finding_from_local_report_to_commit_bound_review(self):
        self.engine.action(dict(op="app_finding", title="Empty report fails", body="An empty report crashes."))
        finding = next(t for t in self.engine.state()["tasks"] if t.get("special") == "app_finding")
        self.key = finding["id"]
        issue = self.prepare("create issue")
        self.assertFalse(issue["worktree"])
        self.approve(issue)
        self.engine.close()
        self.engine = Engine(self.state_dir, lambda: self.now)
        self.engine.add_project(selfcheck.PROJECT, [str(self.repo)])
        self.approve(self.prepare("publish branch"))
        self.approve(self.prepare("create pull request"))
        review = self.prepare("submit PR review")
        self.assertEqual(review["destination"], self.pr_url)
        self.assertEqual(review["payload"]["commit_id"], "newcommit")
        self.pr["base"]["sha"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed after review"):
            self.engine.action(dict(op="approve", id=self.key, version=review["version"]))
        self.assertEqual(len(self.writes), 5)
        self.engine.action(dict(op="retry", id=self.key))
        self.approve(self.prepare("submit PR review"))
        self.assertEqual(self.row()["status"], "done")
        self.assertEqual(self.row()["receipts"][-1]["receipt"], self.pr_url + "#pullrequestreview-1")

    def test_repeated_failed_checks_link_one_finding(self):
        failure = {"test": "test_http.DesktopHTTPTests.test_valid_mutation_is_handled_once",
                   "diagnostic": "unexpected response"}
        with patch.object(selfcheck, "run", return_value={"count": 2, "failures": [failure]}):
            for _ in range(2):
                self.engine.action({"op": "self_check"})
                self.engine.action({"op": "self_check"})
                checks = [t for t in self.engine.state()["tasks"] if t.get("special") == "self_check"
                          and t["status"] == "queued"]
                self.assertEqual(len(checks), 1)
                self.engine.process(checks[0]["id"])
        tasks = self.engine.state()["tasks"]
        findings = [t for t in tasks if t.get("special") == "app_finding"]
        self.assertEqual(len(findings), 1)
        self.assertIn(failure["test"], findings[0]["body"])
        for run in [t for t in tasks if t.get("special") == "self_check"]:
            self.assertEqual(run["findings"], [findings[0]["id"]])
        self.engine.process(findings[0]["id"])
        self.assertIn("Connect one GitHub account", self.engine.db.get("tasks", findings[0]["id"])["result"])

    def test_bundle_under_checkout_does_not_register_resources_as_a_repository(self):
        resources = self.repo / "build/MeMyselfAI.app/Contents/Resources"
        resources.mkdir(parents=True)
        with patch("engine.__file__", str(resources / "engine.py")), patch.object(selfcheck, "REPOSITORY", "fixture/report"):
            self.engine.action({"op": "self_check"})
        self.assertEqual(self.engine.db.get("projects", selfcheck.PROJECT)["repos"], [])

    def test_workspace_github_source_matches_local_project_origin(self):
        other = self.repo.parent / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.engine.action(dict(op="project", name="Reporting", repos=[str(other), str(self.repo)]))
        self.engine.action(dict(op="project", name="Release", repos=[str(self.repo)]))
        self.engine.action(dict(op="project", name="Reporting", parent="Release"))
        self.assertCountEqual(self.engine.repositories("Release"), [str(other), str(self.repo)])
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="project", name="Release", parent="Reporting"))
        self.assertIsNone(self.engine.db.get("projects", "Release").get("parent"))
        raw = dict(
            key="issue:99",
            title="Fix reports",
            body="Please fix this.",
            account="me",
            destination="fixture/report#99",
            context={},
            timestamp=self.now,
        )
        self.engine.ingest(self.github, raw)
        key = digest(self.github["id"], raw["key"])
        with patch("engine.worker.run_agent", return_value={"relevant": True, "project": "Personal"}):
            self.engine.discover(key)
        task = self.engine.db.get("tasks", key)
        self.assertEqual(task["project"], "Reporting")
        paths = self.engine.repository_candidates(task, self.github)
        self.assertEqual(paths, [str(self.repo)])
        self.assertEqual(self.engine.match_repository(task, self.github, paths), str(self.repo))
        task["repository"] = str(self.repo)
        self.assertEqual(self.engine.github_for(task, self.slack)["id"], self.github["id"])
        self.assertIsNone(self.engine.github_for(dict(task, project="Global", repository=""), self.slack))
        self.engine.db.put("connections", dict(self.github, id="other-account"))
        self.assertIsNone(self.engine.github_for(task, self.slack))

    def test_global_ambiguous_checkout_waits_then_resumes_in_chosen_repo(self):
        other = self.repo.parent / "other"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.engine.add_project("Reporting", [str(other), str(self.repo)])
        task = self.engine.new_task("Fix the calculation", "Global", kind="coding")
        with patch("engine.worker.run_agent", return_value={"question": "Which checkout should I use?"}):
            self.engine.process(task["id"])
        pending = self.engine.db.get("tasks", task["id"])
        self.assertTrue(pending["repository_question"])
        self.assertFalse(pending["worktree"])
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="answer", id=task["id"], text="/unregistered"))
        self.engine.action(dict(op="answer", id=task["id"], text=str(self.repo)))
        with patch(
            "engine.worker.run_agent",
            return_value=dict(outcome="done", kind="coding", text="Reviewed the code."),
        ):
            self.engine.process(task["id"])
        done = self.engine.db.get("tasks", task["id"])
        self.assertEqual((done["status"], done["repository"]), ("done", str(self.repo)))
        self.assertTrue((Path(done["worktree"]) / "report.py").is_file())

    def test_conflict_after_notification_preview_prevents_closure(self):
        task = self.run_to_notification()
        self.pr["mergeable"] = False
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.engine.action(dict(op="approve", id=self.key, version=task["version"]))
        self.assertEqual(self.row()["status"], "failed")
        self.assertEqual(len(self.row()["receipts"]), 3)
        self.assertFalse(any(path == "/api/chat.postMessage" for path, _ in self.writes))
