"""Exercise the GitHub milestone workflow with a fixture repository and transport."""

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bugflow as b
import connectors as c


class Github:
    def __init__(self):
        self.calls = []
        self.base = "a" * 40
        self.rev = "b" * 40
        self.repo = "sample/project"
        self.issue = dict(number=4, state="open", assignees=[])
        self.pr = {}

    def __call__(self, conn, method, path, data=None):
        self.calls.append((method, path, data))
        responses = {
            ("GET", ""): dict(default_branch="master", full_name=self.repo),
            ("GET", "issues/4"): self.issue,
            ("GET", "git/ref/heads/master"): {"object": {"sha": self.base}},
            ("GET", "git/commits/" + self.base): {"tree": {"sha": "basetree"}},
            ("POST", "git/trees"): {"sha": "newtree"},
            ("POST", "git/commits"): {"sha": self.rev},
            ("POST", "git/refs"): {"ref": "refs/heads/memyselfai/fix-task1"},
            ("GET", "git/ref/heads/memyselfai/fix-task1"): {"object": {"sha": self.rev}},
            ("GET", "pulls/9"): self.pr,
        }
        if (method, path) == ("POST", "pulls"):
            self.pr = dict(
                number=9,
                html_url="https://github.com/sample/project/pull/9",
                state="open",
                mergeable=True,
                body=data["body"],
                head=dict(ref=data["head"], sha=self.rev, repo={"full_name": self.repo}),
                base=dict(ref=data["base"], repo={"full_name": self.repo}),
            )
            return self.pr
        return responses[(method, path)]


class BugFlow(unittest.TestCase):
    def setUp(self):
        self.api = Github()
        self.conn = {"kind": "github", "config": {"repository": self.api.repo}}
        self.event = {
            "raw": {
                "context": {
                    "repository": self.api.repo,
                    "number": 4,
                    "is_pull_request": False,
                    "author": "reporter",
                },
                "url": "https://github.com/sample/project/issues/4#issuecomment-11",
                "body": "Broken.",
            }
        }
        self.t = dict(
            id="task1",
            worktree="/fixture",
            title="Fix script",
            body="Broken",
            changes=[{"path": "script", "content": "fixed", "mode": "120000"}],
            result="Fixed.",
            milestones={},
        )
        self.who = Mock(return_value="coder")

    def prepared_branch(self):
        with patch.object(
            b,
            "_git",
            side_effect=[
                b"git@github.com:sample/project.git\n",
                self.api.base.encode(),
                b"100755 blob old\tscript\0",
                b"broken",
            ],
        ):
            return b.prepare(self.t, self.event, self.conn, self.api, self.who)

    def test_successful_lifecycle_and_reporter_notification(self):
        prepared = self.prepared_branch()
        self.assertEqual(prepared["operation"], "publish branch")
        self.assertEqual(prepared["payload"]["files"][0]["mode"], "100755")
        self.assertIn("100755", prepared["draft"])
        self.assertTrue(all(row[0] == "GET" for row in self.api.calls))
        self.t.update(prepared)
        _, m = b.send(self.t, self.conn, self.api, self.who)
        self.t["milestones"].update(m)
        self.assertEqual(m["revision"], self.api.rev)
        tree = next(row[2] for row in self.api.calls if row[1] == "git/trees")
        self.assertEqual(tree["tree"], [dict(path="script", type="blob", mode="100755", content="fixed")])
        self.t.update(b.prepare(self.t, self.event, self.conn, self.api, self.who))
        _, m = b.send(self.t, self.conn, self.api, self.who)
        self.t["milestones"].update(m)
        url = b.verify_fix(self.t, self.conn, self.api)
        self.assertIn(url, b.reporter_message(self.event, url))
        self.assertTrue(b.reporter_message(self.event, url).startswith("@reporter "))
        slack = {"raw": {"context": {"channel": "C123", "author": "U123"}}}
        self.assertTrue(b.reporter_message(slack, url).startswith("<@U123> "))
        email = {"raw": {"context": {"recipient": "reporter@example.test"}}}
        self.assertIn(url, b.reporter_message(email, url))
        for key, value in [("body", "Fixes #49"), ("state", "closed"), ("mergeable", False)]:
            with (
                self.subTest(field=key),
                patch.dict(self.api.pr, {key: value}),
                self.assertRaises(c.Rejected),
            ):
                b.verify_fix(self.t, self.conn, self.api)
        for key, value in [
            ("sha", "unknown"),
            ("repo", {"full_name": "other/fork"}),
            ("ref", "unreviewed"),
        ]:
            with (
                self.subTest(field=key),
                patch.dict(self.api.pr["head"], {key: value}),
                self.assertRaises(c.Rejected),
            ):
                b.verify_fix(self.t, self.conn, self.api)
        with patch.dict(self.api.pr, mergeable=None):
            self.assertIsNone(b.verify_fix(self.t, self.conn, self.api))
        with patch.dict(self.api.issue, assignees=[{"login": "other"}]), self.assertRaises(c.Rejected):
            b.verify_fix(self.t, self.conn, self.api)

    def test_pull_request_lists_the_commands_the_app_ran(self):
        self.t["milestones"] = dict(branch="memyselfai/fix-task1", base="master", revision=self.api.rev)
        self.t["verification"] = [
            dict(command="python3 -m unittest\n  tests.test_x", exit_code=0, timed_out=False, platform="Linux x86_64"),
            dict(command="./scripts/build-app.sh", exit_code=None, timed_out=True, platform="Linux x86_64"),
        ]
        body = b.prepare(self.t, self.event, self.conn, self.api, self.who)["payload"]["body"]
        self.assertTrue(body.startswith("Fixes #4\n\nFixed.\n\n## Verification\n"))
        self.assertIn("- `python3 -m unittest tests.test_x` on Linux x86_64: exit 0", body)
        self.assertIn("- `./scripts/build-app.sh` on Linux x86_64: timed out", body)
        self.assertNotIn("Not verified yet", body)
        self.t["unverified"] = ["./scripts/build-app.sh on a Mac"]
        body = b.prepare(self.t, self.event, self.conn, self.api, self.who)["payload"]["body"]
        self.assertIn("Not verified yet:\n\n- ./scripts/build-app.sh on a Mac", body)
        del self.t["verification"]
        body = b.prepare(self.t, self.event, self.conn, self.api, self.who)["payload"]["body"]
        self.assertIn("The agent ran no commands while preparing this fix.", body)

    def test_mismatched_git_identity_ownership_or_base_rejects_writes(self):
        for origin, sha in [
            (b"https://github.com/other/project.git", self.api.base.encode()),
            (b"https://github.com/sample/project.git", b"unpublishedcommit"),
        ]:
            with patch.object(b, "_git", side_effect=[origin, sha]), self.assertRaises(c.Rejected):
                b.prepare(self.t, self.event, self.conn, self.api, self.who)
        self.t.update(self.prepared_branch())
        self.api.issue["assignees"] = [{"login": "other"}]
        with self.assertRaises(c.Rejected):
            b.send(self.t, self.conn, self.api, self.who)
        self.api.issue["assignees"] = []
        self.api.base = "advanced"
        with self.assertRaises(c.Rejected):
            b.send(self.t, self.conn, self.api, self.who)
        with self.assertRaises(c.Rejected):
            b.send(self.t, self.conn, self.api, lambda _: "other")
        self.assertTrue(all(row[0] == "GET" for row in self.api.calls))

    def test_moved_default_branch_publishes_on_the_new_base_unless_it_touched_the_fix(self):
        start = "c" * 40
        compare = ("GET", f"compare/{start}...{self.api.base}")
        fake = self.api

        def call(conn, method, path, data=None):
            if (method, path) == compare:
                fake.calls.append((method, path, data))
                return self.diff
            return Github.__call__(fake, conn, method, path, data)

        def prepare():
            git = Mock(side_effect=[
                b"git@github.com:sample/project.git\n",
                start.encode(),
                b"100755 blob old\tscript\0",
                b"broken",
            ])
            with patch.object(b, "_git", git):
                result = b.prepare(self.t, self.event, self.conn, call, self.who)
            return result, git

        self.diff = dict(status="ahead", files=[dict(filename="README.md")])
        prepared, git = prepare()
        self.assertEqual(prepared["payload"]["sha"], self.api.base)
        self.assertEqual(git.call_args_list[2].args[3], start)
        self.assertEqual(prepared["payload"]["files"][0]["mode"], "100755")
        self.assertTrue(all(row[0] == "GET" for row in self.api.calls))
        for diff in [
            dict(status="ahead", files=[dict(filename="script")]),
            dict(status="ahead", files=[dict(filename="moved", previous_filename="script")]),
            dict(status="diverged", files=[]),
            dict(status="behind", files=[]),
            dict(status="ahead", files=[dict(filename=f"f{i}") for i in range(300)]),
        ]:
            self.diff = diff
            with self.subTest(diff=diff), self.assertRaises(c.Rejected) as caught:
                prepare()
            if diff["status"] == "ahead" and len(diff["files"]) == 1:
                self.assertIn("script", str(caught.exception))

    def test_branch_preview_shows_a_diff_against_the_recorded_base(self):
        """A one-line replacement and a new file in a fixture repository."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        repo = Path(directory.name)

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                 "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args], text=True).strip()

        git("init", "-q", "-b", "master")
        lines = [f"line {i}\n" for i in range(1, 21)]
        (repo / "report.py").write_text("".join(lines))
        (repo / "run.sh").write_text("echo old\n")
        (repo / "big.txt").write_text("".join(f"old {i}\n" for i in range(2000)))
        form = "".join(f"row {i}\n" for i in range(8)) + "page\x0cbreak\nline sep\nlast\r\nend\n"
        (repo / "form.txt").write_bytes(form.encode())
        git("add", ".")
        git("update-index", "--chmod=+x", "run.sh")
        git("commit", "-qm", "Initial fixture")
        git("remote", "add", "origin", "https://github.com/sample/project.git")
        self.api.base = git("rev-parse", "HEAD")
        changed = list(lines)
        changed[9] = "line ten\n"
        self.t.update(worktree=str(repo), changes=[
            dict(path="report.py", content="".join(changed)),
            dict(path="docs/new.md", content="# New\n\nAdded."),
            dict(path="run.sh", content="echo new\n"),
            dict(path="big.txt", content="".join(f"new {i}\n" for i in range(2000))),
            dict(path="form.txt", content=form.replace("end\n", "END\n")),
        ])
        prepared = b.prepare(self.t, self.event, self.conn, self.api, self.who)
        self.assertEqual(prepared["operation"], "publish branch")
        self.assertTrue(all(set(f) == {"path", "mode", "content"} for f in prepared["payload"]["files"]))
        preview = json.loads(prepared["draft"])
        self.assertEqual(preview["repository"], "sample/project")
        self.assertEqual(preview["sha"], self.api.base)
        self.assertEqual(preview["files"], prepared["payload"]["files"])
        diffs = {d["path"]: d for d in preview["diffs"]}
        self.assertEqual(list(diffs), ["report.py", "docs/new.md", "run.sh", "big.txt", "form.txt"])

        # Lines end only at \n, as in Git, so form feeds and U+2028 stay inside a line.
        (repo / "form.txt").write_bytes(form.replace("end\n", "END\n").encode())
        expected = subprocess.check_output(["git", "-C", str(repo), "diff", "--no-color", "-U3", "--", "form.txt"])
        expected = expected.decode().split("\n")
        hunk = next(i for i, l in enumerate(expected) if l.startswith("@@"))
        expected = [expected[hunk].rsplit("@@", 1)[0] + "@@"] + expected[hunk + 1:-1]  # drop Git's function context
        self.assertEqual(diffs["form.txt"]["diff"][2:], expected)
        self.assertEqual((diffs["form.txt"]["additions"], diffs["form.txt"]["deletions"]), (1, 1))

        report = diffs["report.py"]
        self.assertEqual((report["status"], report["additions"], report["deletions"]), ("modified", 1, 1))
        self.assertEqual(report["diff"][:3], ["--- a/report.py", "+++ b/report.py", "@@ -7,7 +7,7 @@"])
        self.assertEqual(report["diff"][3:], [" line 7", " line 8", " line 9", "-line 10", "+line ten",
                                              " line 11", " line 12", " line 13"])
        self.assertFalse(report["truncated"])

        new = diffs["docs/new.md"]
        self.assertEqual((new["status"], new["additions"], new["deletions"]), ("added", 3, 0))
        self.assertEqual(new["diff"][:2], ["--- /dev/null", "+++ b/docs/new.md"])
        self.assertEqual(new["diff"][-4:], ["+# New", "+", "+Added.", "\\ No newline at end of file"])

        script = diffs["run.sh"]
        self.assertEqual(script["mode"], "100755")
        self.assertEqual(script["diff"][-2:], ["-echo old", "+echo new"])

        big = diffs["big.txt"]
        self.assertTrue(big["truncated"])
        self.assertEqual((big["additions"], big["deletions"]), (2000, 2000))
        self.assertLessEqual(len(big["diff"]), b.DIFF_LINES + 2)
        self.assertTrue(all(row[0] == "GET" for row in self.api.calls))

    def test_a_moved_base_at_approval_is_reported_as_such_without_a_write(self):
        self.t.update(self.prepared_branch())
        self.api.base = "advanced"
        with self.assertRaises(b.BaseMoved):
            b.send(self.t, self.conn, self.api, self.who)
        self.assertTrue(all(row[0] == "GET" for row in self.api.calls))

    def test_unresolved_conflict_markers_are_never_proposed(self):
        self.t["changes"] = [dict(path="script", content="<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> edits\n")]
        with self.assertRaises(c.Rejected) as caught:
            self.prepared_branch()
        self.assertIn("script", str(caught.exception))
        self.assertIn("conflict", str(caught.exception))

    def test_reconcile_confirms_only_the_approved_tree(self):
        self.t.update(self.prepared_branch())
        sha = hashlib.sha1(b"blob 5\x00fixed").hexdigest()
        entries = [dict(path="script", mode="100755", type="blob", sha=sha)]

        def api(conn, method, path, data=None):
            self.assertEqual(method, "GET")
            return {
                "git/ref/heads/memyselfai/fix-task1": {"object": {"sha": "revision"}},
                "git/commits/revision": dict(
                    parents=[{"sha": self.api.base}], message=self.t["title"], tree={"sha": "after"}
                ),
                "git/commits/" + self.api.base: {"tree": {"sha": "before"}},
                "git/trees/before": {"tree": []},
                "git/trees/after": {"tree": entries},
            }[path]

        self.assertEqual(b.reconcile_branch(self.t, self.conn, api)["revision"], "revision")
        entries.append(dict(path="extra", mode="100644", type="blob", sha=sha))
        with self.assertRaises(c.Rejected):
            b.reconcile_branch(self.t, self.conn, api)

    def test_issue_from_comment_context_and_email_link(self):
        self.assertEqual(b.issue_number(self.event, self.conn), 4)
        raw = {"url": "", "body": "See https://github.com/sample/project/issues/17."}
        self.assertEqual(b.issue_number({"raw": raw}, self.conn), 17)
        pr = copy.deepcopy(self.event)
        pr["raw"]["context"]["is_pull_request"] = True
        pr["raw"]["url"] = "https://github.com/sample/project/pull/4#issuecomment-22"
        self.assertIsNone(b.issue_number(pr, self.conn))
