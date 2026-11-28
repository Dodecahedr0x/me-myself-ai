"""Run the desktop bug workflow with local Git and fixture provider transports."""

import json
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
        # The fixture's origin is a made-up GitHub address: task copies start from the local folder, as offline.
        offline = patch("worker._fetch_remote", return_value=False)
        offline.start()
        self.addCleanup(offline.stop)
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

    def model(self, role, payload, model, workdir):
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
        if suffix.startswith("/compare/"):
            return dict(status="ahead", files=[dict(filename=n) for n in getattr(self, "compare_files", [])]), {}
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
            if expected == "publish branch":
                diff = json.loads(task["draft"])["diffs"]
                self.assertEqual([d["path"] for d in diff], ["report.py"])
                self.assertEqual(diff[0]["diff"][2:], ["@@ -1,2 +1,2 @@", " def total(values):",
                                                       "-    return values[0]", "+    return sum(values)"])
                with self.assertRaisesRegex(ValueError, "preview changed"):
                    self.engine.action(dict(op="approve", id=self.key, version=task["version"] - 1))
                self.assertEqual(len(self.writes), count - 3)
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

    def own_issue(self):
        raw = dict(key="issue:fixture/report:7", title="Empty report fails", body="Sum the values.",
                   account="fixture-coder", destination="fixture/report#7", timestamp=self.now,
                   authored=True, owned=False, url="https://github.com/fixture/report/issues/7",
                   context=dict(repository="fixture/report", number=7, is_pull_request=False,
                                state="open", author="fixture-coder", assignees=[]))
        self.engine.ingest(self.github, raw)
        event = digest(self.github["id"], raw["key"])
        return self.engine.new_task("Empty report fails", "Reporting", event=event, kind="coding", id=event)

    def test_finished_issue_edits_become_a_fix_branch_and_pr(self):
        task = self.own_issue()
        self.key = task["id"]
        coded = dict(outcome="done", kind="coding", text="Sum the values.",
                     files=[dict(path="report.py", content=self.fixed)])
        with patch("engine.worker.run_agent", return_value=coded):
            branch = self.prepare("publish branch")
        self.assertEqual(self.row()["kind"], "bug")
        self.approve(branch)
        self.approve(self.prepare("create pull request"))
        self.engine.process(self.key)
        done = self.row()
        # The user wrote the issue, so the verified PR finishes it without a notice to themselves.
        self.assertEqual(done["status"], "done", done["result"])
        self.assertIn(self.pr_url, done["result"])
        self.assertEqual([p.rsplit("/", 1)[-1] for p, _ in self.writes], ["trees", "commits", "refs", "pulls"])

    def advance(self, files):
        """Move the folder's origin/main past the fix's base, as a maintainer's fetch would, leaving the folder's
        branch, index and uncommitted files alone. The fixture GitHub reports the same new head."""
        def git(*args, stdin=None):
            return subprocess.run(["git", "-C", str(self.repo), *args], input=stdin, capture_output=True,
                                  text=True, check=True).stdout.strip()
        entries = git("ls-tree", self.base).splitlines()
        tree = {line.split("\t")[1]: line for line in entries}
        for name, content in files.items():
            blob = git("hash-object", "-w", "--stdin", stdin=content)
            tree[name] = f"100644 blob {blob}\t{name}"
        sha = git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit-tree",
                  git("mktree", stdin="\n".join(tree.values()) + "\n"), "-p", self.base, "-m", "Advance main")
        git("update-ref", "refs/remotes/origin/main", sha)
        self.base = sha
        return sha

    def stale_fix_after_main_moves(self, files):
        task = self.own_issue()
        self.key = task["id"]
        coded = dict(outcome="done", kind="coding", text="Sum the values.",
                     files=[dict(path="report.py", content=self.fixed)])
        with patch("engine.worker.run_agent", return_value=coded):
            stale = self.prepare("publish branch")
        old = self.base
        self.assertEqual(stale["payload"]["sha"], old)
        self.advance(files)
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="approve", id=self.key, version=stale["version"]))
        self.assertEqual(self.writes, [], "The old approval produced no remote write")
        row = self.row()
        self.assertEqual(row["status"], "queued", row["result"])
        self.assertIn("default branch", row["reason"])
        return stale, old

    def finish_replacement(self, stale, old, agent):
        with patch("engine.worker.run_agent", side_effect=agent):
            fresh = self.prepare("publish branch")
        self.assertEqual(fresh["payload"]["sha"], self.base)
        self.assertNotEqual(fresh["payload"]["sha"], old)
        self.assertGreater(fresh["version"], stale["version"])
        self.assertEqual(fresh["event"], stale["event"])
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="approve", id=self.key, version=stale["version"]))
        self.assertEqual(self.writes, [], "Only the replacement proposal can be approved")
        self.approve(fresh)
        self.assertEqual([p.rsplit("/", 1)[-1] for p, _ in self.writes], ["trees", "commits", "refs"])
        self.assertEqual(self.file.read_text(), "# User's outstanding changes\n")
        kinds = [kind for kind, in self.engine.db.conn.execute("SELECT kind FROM activity WHERE task=?", (self.key,))]
        self.assertIn("Moved to the new default branch", kinds)
        self.assertIn("Files edited", kinds, "The original task history is kept")
        return fresh

    def test_fix_moves_onto_an_advanced_default_branch_and_needs_fresh_approval(self):
        stale, old = self.stale_fix_after_main_moves({"NOTES.md": "Release notes\n"})

        def agent(role, payload, model, workdir):
            self.assertEqual(payload["base_moved"]["conflicts"], [])
            self.assertEqual(payload["files"]["report.py"], self.fixed)
            self.assertEqual(payload["files"]["NOTES.md"], "Release notes\n")
            return dict(outcome="done", kind="bug", text="Sum the values; checks rerun on the new base.")
        self.finish_replacement(stale, old, agent)

    def test_overlapping_default_branch_edits_are_resolved_before_a_new_proposal(self):
        stale, old = self.stale_fix_after_main_moves(
            {"report.py": "def total(values):\n    return values[0] if values else 0\n"})
        self.fixed = "def total(values):\n    return sum(values) if values else 0\n"

        def agent(role, payload, model, workdir):
            self.assertEqual(payload["base_moved"]["conflicts"], ["report.py"])
            marked = payload["files"]["report.py"]
            self.assertIn("<<<<<<<", marked)
            self.assertIn("return sum(values)", marked)
            self.assertIn("if values else 0", marked)
            return dict(outcome="done", kind="bug", text="Resolved the overlap.",
                        files=[dict(path="report.py", content=self.fixed)])
        self.finish_replacement(stale, old, agent)

    def test_an_interrupted_refresh_never_publishes_old_contents_on_the_new_base(self, legacy=False):
        stale, old = self.stale_fix_after_main_moves(
            {"report.py": '"""Report helpers."""\n\n\ndef total(values):\n    return values[0]\n'})
        row = self.row()
        # Stored milestones send a finished bug straight to bugflow without the agent.
        row["milestones"] = dict(issue=7, repository="fixture/report")
        if legacy:
            del row["changes_base"]  # saved by a build that did not record the base
        self.engine.db.put("tasks", row)
        with patch("worker._replace_file", side_effect=OSError("disk full")):
            self.engine.process(self.key)
        failed = self.row()
        self.assertEqual(failed["status"], "failed", failed["result"])
        self.assertEqual(failed["changes_base"], old, "The saved edits still record their own base")
        self.assertEqual(subprocess.check_output(["git", "-C", failed["worktree"], "rev-parse", "HEAD"],
                                                 text=True).strip(), self.base, "The clone moved before the failure")
        self.engine.action(dict(op="retry", id=self.key))
        self.fixed = '"""Report helpers."""\n\n\ndef total(values):\n    return sum(values)\n'

        def agent(role, payload, model, workdir):
            self.assertEqual(payload["base_moved"]["conflicts"], [])
            self.assertEqual(payload["files"]["report.py"], self.fixed, "Both the fix and main's change are kept")
            return dict(outcome="done", kind="bug", text="Sum the values on the new base.")
        fresh = self.finish_replacement(stale, old, agent)
        self.assertEqual(fresh["payload"]["files"][0]["content"], self.fixed)

    def test_an_interrupted_first_refresh_of_edits_saved_without_a_base(self):
        self.test_an_interrupted_refresh_never_publishes_old_contents_on_the_new_base(legacy=True)

    def test_an_unresolved_overlap_blocks_any_proposal_until_resolved(self):
        stale, old = self.stale_fix_after_main_moves(
            {"report.py": "def total(values):\n    return values[0] if values else 0\n"})
        omit = dict(outcome="done", kind="bug", text="Done.")
        with patch("engine.worker.run_agent", return_value=omit):
            self.engine.process(self.key)
        blocked = self.row()
        self.assertEqual(blocked["status"], "failed")
        self.assertIn("report.py", blocked["result"])
        self.assertEqual(blocked["base_moved"]["conflicts"], ["report.py"])
        self.assertIn("<<<<<<<", next(c["content"] for c in blocked["changes"] if c["path"] == "report.py"))
        self.assertEqual(self.writes, [])
        b_head = self.base

        # Main moves again before the overlap is resolved: the clone stays put and the overlap is kept.
        self.advance({"NOTES.md": "Release notes\n"})
        self.compare_files = ["NOTES.md"]
        self.engine.action(dict(op="retry", id=self.key))
        with patch("engine.worker.run_agent", return_value=omit):
            self.engine.process(self.key)
        again = self.row()
        self.assertEqual((again["status"], again["base_moved"]["conflicts"]), ("failed", ["report.py"]))
        self.assertEqual(again["changes_base"], b_head)
        self.assertEqual(self.writes, [])

        self.engine.action(dict(op="retry", id=self.key))
        self.fixed = "def total(values):\n    return sum(values) if values else 0\n"

        def agent(role, payload, model, workdir):
            self.assertEqual(payload["base_moved"]["conflicts"], ["report.py"])
            return dict(outcome="done", kind="bug", text="Resolved the overlap.",
                        files=[dict(path="report.py", content=self.fixed)])
        fresh = self.finish_replacement(stale, old, agent)
        self.assertIsNone(self.row()["base_moved"])
        self.assertEqual(fresh["payload"]["files"], [dict(path="report.py", mode="100644", content=self.fixed)])

    def test_restart_reopens_issue_work_closed_without_a_branch(self):
        task = self.own_issue()
        with patch("engine.worker.run_agent", return_value=dict(outcome="question", text="Which output?")):
            self.engine.process(task["id"])
        stale = self.engine.db.get("tasks", task["id"])
        old = Path(stale["worktree"])
        self.assertTrue(old.is_dir())
        stale.update(status="done", changes=[dict(path="report.py", content=self.fixed)], result="Edited")
        self.engine.db.put("tasks", stale)
        self.engine.close()
        self.engine = Engine(self.state_dir, lambda: self.now)
        reopened = self.engine.db.get("tasks", task["id"])
        self.assertEqual((reopened["status"], reopened["changes"], reopened["worktree"]), ("queued", [], ""))
        self.assertFalse(old.exists())
        self.assertTrue(any(p.name.startswith(old.name + ".set-aside-") for p in old.parent.iterdir()))
        self.engine.close()
        self.engine = Engine(self.state_dir, lambda: self.now)
        self.assertEqual(self.engine.db.get("tasks", task["id"])["version"], reopened["version"], "Recovery runs once")

    def test_pull_request_review_gets_the_checkout_on_first_attempt(self):
        raw = dict(key="issue:fixture/report:26", title="Verify and merge PR #26", body="Fixes the sum.",
                   account="me", destination="fixture/report#26", timestamp=self.now,
                   context=dict(repository="fixture/report", number=26, is_pull_request=True, state="open"))
        self.engine.ingest(self.github, raw)
        event = digest(self.github["id"], raw["key"])
        task = self.engine.new_task("Verify and merge PR #26", "Reporting", event=event, kind="pull_request")
        with patch("engine.worker.run_agent", side_effect=[
            dict(outcome="question", text="Merge now?"), dict(question="Merge now?"),
        ]) as agent:
            self.engine.process(task["id"])
        prepared = self.engine.db.get("tasks", task["id"])
        self.assertEqual(prepared["repository"], str(self.repo), prepared["result"])
        self.assertTrue((Path(prepared["worktree"]) / "report.py").is_file())
        first = agent.call_args_list[0]
        self.assertEqual(first.args[3], Path(prepared["worktree"]))
        self.assertIn("report.py", first.args[1]["files"])
        self.assertEqual(first.args[1]["local_commands_remaining"], 4)

    def test_issue_agents_receive_selected_checkout_on_first_attempt(self):
        raw = dict(key="issue:17", title="Inspect report", body="Inspect the calculation",
                   account="me", destination="fixture/report#17", timestamp=self.now,
                   context=dict(repository="fixture/report", number=17, is_pull_request=False))
        self.engine.ingest(self.github, raw)
        event = digest(self.github["id"], raw["key"])
        for kind in ("issue", "coding"):
            task = self.engine.new_task("Inspect report", "Reporting", event=event, kind=kind)
            with patch("engine.worker.run_agent", side_effect=[
                dict(outcome="question", text="Which output do you want?"),
                dict(question="Which output do you want?"),
            ]) as agent:
                self.engine.process(task["id"])
            prepared = self.engine.db.get("tasks", task["id"])
            self.assertEqual(prepared["status"], "question", prepared["result"])
            for call in agent.call_args_list:
                context = call.args[1]["task"]
                self.assertEqual(call.args[3], Path(prepared["worktree"]))
                self.assertEqual(context["repository"], str(self.repo))
                self.assertEqual(context["worktree"], prepared["worktree"])
                self.assertEqual(context["github_repository"], "fixture/report")
            self.assertTrue((Path(prepared["worktree"]) / "report.py").is_file())
            self.assertIn("report.py", agent.call_args_list[0].args[1]["files"])

    def test_delegated_step_uses_parent_evidence_and_separate_worktree(self):
        parent = self.engine.new_task("Fix the report", "Reporting", "Check negative totals.", kind="coding")
        with patch("worker.run_agent", side_effect=[
            dict(outcome="question", text="What output?", independent_steps=[
                dict(title="Inspect report.py", body="Explain the current calculation.")]),
            dict(question="What output?"),
        ]):
            self.engine.process(parent["id"])
        child = digest(parent["id"], "Inspect report.py")
        with patch("worker.run_agent", return_value=dict(outcome="done", text="Uses the first value.")) as agent:
            self.engine.process(child)
        payload = agent.call_args.args[1]
        self.assertEqual(payload["parent_task"]["body"], "Check negative totals.")
        self.assertIn("report.py", payload["files"])
        self.assertNotEqual(self.engine.db.get("tasks", parent["id"])["worktree"], str(agent.call_args.args[3]))
        # The child finished while its parent awaited a decision. Resume through the scheduler.
        self.engine.suggestions.next_scan = self.now + 3600
        for conn in self.engine.db.rows("connections"):
            self.engine.db.put("connections", dict(conn, next=self.now + 3600))
        with patch("worker.run_agent", side_effect=[
            dict(outcome="question", text="What output?"), dict(question="What output?"),
        ]) as agent:
            self.engine.tick()
            for job in list(self.engine.jobs.values()):
                job.result(timeout=5)
            self.assertEqual(agent.call_args_list[0].args[1]["children"][0]["result"], "Uses the first value.")
            self.engine.tick()
            self.assertNotIn("task:" + parent["id"], self.engine.jobs)

    def test_legacy_git_permission_failure_retries_only_once(self):
        error = f"Git cannot access {self.repo}. Check MeMyselfAI's folder permissions, then retry."
        task = self.engine.new_task("Prepare the report", "Reporting", status="failed", kind="coding",
                                    repository=str(self.repo), result=error)
        self.engine.resume_repository_questions()
        retry = self.engine.db.get("tasks", task["id"])
        self.assertEqual(retry["status"], "queued")
        self.engine.db.put("tasks", dict(retry, status="failed", result=error))
        self.engine.resume_repository_questions()
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "failed")

    def test_command_blocked_step_retries_once_and_uses_the_output(self):
        task = self.engine.new_task("Run both fixture suites in this checkout", "Reporting", kind="coding",
                                    status="question", context_checked=True, repository=str(self.repo),
                                    question="I can't run commands in this checkout. Please paste the output.")
        self.engine.resume_repository_questions()
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "queued")
        responses = [dict(outcome="run", text="exit 7"), dict(outcome="question", text="Which release?"),
                     dict(question="Which release?")]
        with patch("worker.run_agent", side_effect=responses) as agent, patch(
                "worker.run_command", return_value=dict(exit_code=7, output="fixture failed")) as command:
            self.engine.process(task["id"])
            command.assert_called_once()
            self.assertEqual(agent.call_args_list[1].args[1]["command_results"][-1]["exit_code"], 7)
        self.engine.resume_repository_questions()
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "question")

    def test_spent_command_budget_gets_one_wrap_up_turn_instead_of_failing(self):
        task = self.engine.new_task("Run the fixture suite in this checkout", "Reporting", kind="coding",
                                    status="question", context_checked=True, repository=str(self.repo),
                                    question="I can't run commands in this checkout. Please paste the output.")
        self.engine.resume_repository_questions()
        responses = [dict(outcome="run", text="exit 1")] * 5 + [
            dict(outcome="question", text="Which release?"), dict(question="Which release?")]
        with patch("worker.run_agent", side_effect=responses) as agent, patch(
                "worker.run_command", return_value=dict(exit_code=1, output="failed")) as command:
            self.engine.process(task["id"])
        self.assertEqual(command.call_count, 4, "the fifth request is not run")
        final = agent.call_args_list[5].args[1]
        self.assertTrue(final["command_budget_exhausted"])
        self.assertEqual(final["local_commands_remaining"], 0)
        self.assertEqual(len(final["command_results"]), 4)
        row = self.engine.db.get("tasks", task["id"])
        self.assertEqual(row["status"], "question")
        self.assertTrue(any(r["kind"] == "Command budget spent" for r in self.engine.db.history()))

    def test_command_request_after_wrap_up_still_fails(self):
        task = self.engine.new_task("Run the fixture suite in this checkout", "Reporting", kind="coding",
                                    status="question", context_checked=True, repository=str(self.repo),
                                    question="I can't run commands in this checkout. Please paste the output.")
        self.engine.resume_repository_questions()
        with patch("worker.run_agent", return_value=dict(outcome="run", text="exit 1")), patch(
                "worker.run_command", return_value=dict(exit_code=1, output="failed")) as command:
            self.engine.process(task["id"])
        self.assertEqual(command.call_count, 4)
        row = self.engine.db.get("tasks", task["id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("No further local commands", row["result"])

    def test_setup_step_installs_without_asking_and_returns_its_result(self):
        task = self.engine.new_task("Run the Go tests in this checkout", "Reporting", kind="coding",
                                    status="question", context_checked=True, repository=str(self.repo),
                                    question="I can't run commands in this checkout. Please paste the output.")
        self.engine.resume_repository_questions()
        responses = [dict(outcome="setup", text="install go"), dict(outcome="setup", text="install python"),
                     dict(outcome="question", text="Which release?"), dict(question="Which release?")]
        with patch("worker.run_agent", side_effect=responses) as agent, \
                patch("toolchains.install", return_value="Installed go1.99.1") as install:
            self.engine.process(task["id"])
        install.assert_called_once_with(self.engine.db.root / "toolchains", "go")
        second = agent.call_args_list[1].args[1]
        self.assertEqual(second["setup_steps_remaining"], 3)
        self.assertEqual(second["local_commands_remaining"], 4)
        self.assertEqual(second["command_results"][-1]["output"], "Installed go1.99.1")
        third = agent.call_args_list[2].args[1]
        self.assertEqual(third["command_results"][-1]["exit_code"], 1, "an unlisted step is refused")
        self.assertTrue(any(r["kind"] == "Setup step" for r in self.engine.db.history()))

    def test_existing_checkout_question_resumes_once(self):
        task = self.engine.new_task("Prepare the report", "Reporting", kind="coding", status="question",
                                    question="Can you link the worktree or paste report.py?")
        unrelated = self.engine.new_task("Approve plan", "Reporting", status="question", question="Which date?")
        self.engine.resume_repository_questions()
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "queued")
        with patch("worker.run_agent", side_effect=[
            dict(outcome="question", text="Which date?"), dict(question="Which date?"),
        ]):
            self.engine.process(task["id"])
        self.engine.resume_repository_questions()
        for row in (task, unrelated):
            self.assertEqual(self.engine.db.get("tasks", row["id"])["status"], "question")

    def test_question_edits_survive_decision_failure_and_later_preparation(self):
        task = self.engine.new_task("Inspect report.py", "Reporting", kind="coding")
        first = dict(outcome="question", text="What should the output say?",
                     files=[dict(path="notes.txt", content="Reviewed the input")])
        with patch("worker.run_agent", side_effect=[first, RuntimeError("Offline")]):
            self.engine.process(task["id"])
        row = self.engine.db.get("tasks", task["id"])
        self.assertEqual(row["status"], "failed")
        self.assertEqual((Path(row["worktree"]) / "notes.txt").read_text(), "Reviewed the input")
        self.assertEqual(row["changes"], first["files"])
        self.engine.action(dict(op="retry", id=task["id"]))
        last = dict(outcome="done", text="Report fixed.", files=[dict(path="report.py", content=self.fixed)])
        with patch("worker.run_agent", return_value=last):
            self.engine.process(task["id"])
        done = self.engine.db.get("tasks", task["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["changes"], first["files"] + last["files"])
        self.assertFalse(self.writes)

    def test_agent_timeout_retries_preserve_worktree_and_stop(self):
        import worker
        task = self.engine.new_task("Inspect report.py", "Reporting", kind="coding")
        with patch("worker.run_agent", side_effect=worker.AgentTimeout("CLI timed out")) as agent:
            for delay in (30, 120):
                self.engine.process(task["id"])
                row = self.engine.db.get("tasks", task["id"])
                self.assertEqual((row["status"], row["due"]), ("scheduled", self.now + delay))
                self.now += delay
            self.engine.process(task["id"])
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "failed")
        self.assertEqual(len({c.args[3] for c in agent.call_args_list}), 1)
        self.assertEqual([c.kwargs.get("timeout") for c in agent.call_args_list], [None, 360, 540])

    def test_transient_checkout_timeouts_back_off_and_stop(self):
        task = self.engine.new_task("Inspect report", "Reporting", kind="coding")
        with patch("worker.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 60)):
            for delay in (30, 120):
                self.engine.process(task["id"])
                row = self.engine.db.get("tasks", task["id"])
                self.assertEqual((row["status"], row["due"]), ("scheduled", self.now + delay))
                self.engine.process(task["id"])
                self.assertEqual(self.engine.db.get("tasks", task["id"])["version"], row["version"])
                self.now += delay
            self.engine.process(task["id"])
        row = self.engine.db.get("tasks", task["id"])
        self.assertEqual(row["status"], "failed")
        self.assertIn("timed out", row["result"])

    def test_named_and_requested_files_precede_generic_context(self):
        task = self.engine.new_task("Inspect z_target.py", "Reporting", kind="coding")
        from worker import prepare_worktree
        tree = prepare_worktree(self.repo, self.state_dir / "worktrees", task["id"])
        for n in range(25):
            (tree / f"a{n:02}.py").write_text("fixture")
        (tree / "z_target.py").write_text("target")
        (tree / "z_other.py").write_text("other")
        with patch("worker.run_agent", side_effect=[
            dict(outcome="question", text="Please provide z_other.py"),
            dict(outcome="done", text="Both files inspected."),
        ]) as agent:
            self.engine.process(task["id"])
        first, second = [c.args[1] for c in agent.call_args_list]
        self.assertEqual(first["files"]["z_target.py"], "target")
        self.assertNotIn("z_other.py", first["files"])
        self.assertIn("z_other.py", first["available_paths"])
        self.assertEqual(second["files"]["z_other.py"], "other")
        self.assertLessEqual(len(second["files"]), 24)
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "done")

    def unregistered_source(self):
        self.engine.add_project("Reporting", [])
        home = self.repo.parent
        root = home / "Documents" / "team" / "services"
        root.mkdir(parents=True)
        self.repo = self.repo.rename(root / "report")
        home_patch = patch.object(Path, "home", return_value=home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        raw = dict(key="issue:33", title="Inspect calculation", body="", account="me",
                   timestamp=self.now, destination="fixture/report#33",
                   context=dict(repository="fixture/report", number=33, is_pull_request=False))
        self.engine.ingest(self.github, raw)
        return digest(self.github["id"], raw["key"])

    def test_new_issue_infers_project_from_local_origin_and_uses_private_clone(self):
        event = self.unregistered_source()
        with patch("worker.run_agent", return_value=dict(relevant=True, kind="coding")):
            self.engine.discover(event)
        task = self.engine.db.get("tasks", event)
        self.assertEqual(task["project"], "fixture/report")
        self.assertEqual(self.engine.repositories(task["project"]), [str(self.repo)])
        with patch("worker.run_agent", return_value=dict(outcome="done", text="Checked the report.")):
            self.engine.process(event)
        done = self.engine.db.get("tasks", event)
        self.assertEqual(done["status"], "done", done["result"])
        self.assertEqual(done["repository"], str(self.repo))
        self.assertTrue((Path(done["worktree"]) / "report.py").is_file())

    def test_waiting_issue_verifies_owner_and_refreshes_checkout_buttons(self):
        event = self.unregistered_source()
        task = self.engine.new_task("Inspect calculation", "Global", kind="coding", event=event,
                                    status="question", repository_question=True, answer_choices=[])
        other = self.repo.parents[1] / "archive" / "report"
        other.parent.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run(["git", "-C", str(other), "remote", "add", "origin", "git@github.com:fixture/report.git"], check=True)
        self.engine.resume_repository_questions()
        pending = self.engine.db.get("tasks", task["id"])
        choices = pending["answer_choices"]
        self.assertEqual(pending["status"], "question")
        self.assertEqual({c["detail"] for c in choices}, {str(other), str(self.repo)})
        self.assertEqual(len({c["label"] for c in choices}), 2)
        subprocess.run(["git", "-C", str(other), "remote", "set-url", "origin", "git@github.com:other/report.git"], check=True)
        self.engine.resume_repository_questions()
        queued = self.engine.db.get("tasks", task["id"])
        self.assertEqual((queued["status"], queued["project"], queued["repository"]),
                         ("queued", "fixture/report", str(self.repo)))
        self.assertEqual(self.engine.db.get("events", event)["project"], queued["project"])

    def test_missing_checkout_can_be_selected_in_task(self):
        event = self.unregistered_source()
        self.repo = self.repo.rename(self.repo.parent / "custom-folder")
        task = self.engine.new_task("Inspect calculation", "Global", kind="coding", event=event,
                                    status="question", repository_question=True)
        action = dict(op="answer", id=task["id"], version=task["version"], picked_folder=True)
        other = self.repo.parent / "wrong"
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        with self.assertRaisesRegex(ValueError, "fixture/report"):
            self.engine.action(dict(action, text=str(other)))
        self.engine.action(dict(action, text=str(self.repo)))
        queued = self.engine.db.get("tasks", task["id"])
        self.assertEqual((queued["status"], queued["repository"]), ("queued", str(self.repo)))

    def test_registered_checkout_resumes_repository_selection_questions(self):
        self.engine.add_project("Reporting", [])
        task = self.engine.new_task("Fix report", "Reporting", kind="coding")
        with patch("engine.worker.run_agent", return_value=dict(question="Which checkout?")):
            self.engine.process(task["id"])
        waiting = self.engine.db.get("tasks", task["id"])
        self.assertTrue(waiting["repository_question"])
        other = self.engine.new_task("Choose a release date", "Reporting", status="question")
        self.engine.action(dict(op="project", name="Reporting", repos=[str(self.repo)]))
        self.assertTrue(self.engine.repository_recheck.is_set())
        self.engine.resume_repository_questions()
        resumed = self.engine.db.get("tasks", task["id"])
        self.assertEqual((resumed["status"], resumed["repository"]), ("queued", str(self.repo)))
        self.assertEqual(self.engine.db.get("tasks", other["id"])["status"], "question")
        self.engine.db.put("tasks", waiting)
        reopened = Engine(self.state_dir)
        self.addCleanup(reopened.close)
        reopened.suggestions.next_scan = reopened.now() + 3600
        for connection in reopened.db.rows("connections"):
            reopened.db.put("connections", dict(connection, next=reopened.now() + 3600))
        with patch.object(reopened, "process"):
            reopened.tick()
            reopened.jobs["repository-checks"].result(timeout=5)
        self.assertEqual(reopened.db.get("tasks", task["id"])["status"], "queued")
        self.assertEqual(reopened.db.get("tasks", other["id"])["status"], "question")

    def test_retry_local_question_can_use_project_checkout(self):
        task = self.engine.new_task("Update the report", "Reporting", status="question",
                                    question="Can you link a checkout?")
        self.engine.action(dict(op="retry", id=task["id"]))
        with patch("engine.worker.run_agent", side_effect=[
            dict(outcome="question", kind="code", text="Prepare the worktree."),
            dict(outcome="done", text="Read the calculation."),
        ]) as agent:
            self.engine.process(task["id"])
            initial = agent.call_args_list[0].args[1]
            self.assertEqual(agent.call_args_list[0].args[3].parent, self.state_dir / "agent-workspaces")
            self.assertEqual(initial["registered_checkout"],
                             dict(path=str(self.repo), github_repository="fixture/report"))
            self.assertFalse(initial["task"]["question"])
            self.engine.process(task["id"])
            self.assertIn("report.py", agent.call_args_list[1].args[1]["files"])
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "done")

    def test_registered_folder_timeout_is_distinct_from_missing_checkout(self):
        raw = dict(key="issue:19", title="Fix report", body="", account="me", timestamp=self.now,
                   destination="fixture/report#19", context=dict(repository="fixture/report"))
        self.engine.ingest(self.github, raw)
        event = digest(self.github["id"], raw["key"])
        waiting = [self.engine.new_task("Fix report", "Reporting", kind="coding", event=event,
                                       status="question", repository_question=True) for _ in range(2)]
        self.engine.action(dict(op="ignorelist", projects=[], repositories=["elsewhere/ignored"]))
        with patch("bugflow._git", side_effect=subprocess.TimeoutExpired("git", 15)) as git:
            self.engine.resume_repository_questions()
            git.assert_called_once()
        for task in waiting:
            current = self.engine.db.get("tasks", task["id"])
            self.assertEqual(current["status"], "question")
            self.assertIn(str(self.repo), current["question"])
            self.assertIn("macOS folder access", current["question"])
        self.engine.suggestions.next_scan = self.now + 3600
        for conn in self.engine.db.rows("connections"):
            self.engine.db.put("connections", dict(conn, next=self.now + 3600))
        self.engine.repository_recheck.clear()
        self.now += 61
        with patch.object(self.engine, "discover"):
            self.engine.tick()
            self.engine.jobs["repository-checks"].result(timeout=5)
        self.assertTrue(all(self.engine.db.get("tasks", t["id"])["status"] == "queued" for t in waiting))

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
