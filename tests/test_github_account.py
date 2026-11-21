"""Workspace GitHub account inventory and repository-specific discovery."""

import tempfile
import unittest
from unittest.mock import patch

import connectors
from engine import Engine, digest


class GitHubAccountTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = Engine(folder.name, lambda: 10000)
        self.addCleanup(self.engine.close)
        self.engine.connect_github_account("demo", None)
        (self.account,) = self.engine.state()["connections"]

    def refresh(self, *repos):
        with patch("connectors.github_repositories", return_value=repos):
            self.engine.sync(self.account)

    def children(self):
        return {
            c["config"]["repository"]: c
            for c in self.engine.db.rows("connections")
            if c.get("github_parent")
        }

    def test_repository_membership_failure_recovery_and_disconnect(self):
        self.refresh()
        self.assertEqual(self.engine.state()["connections"][0]["status"], "Connected")
        self.refresh("demo/one", "org/two")
        first, second = self.children().values()
        self.engine.connection_result(first, "Connected", cursor=9990)
        with patch("connectors.github_repositories", side_effect=RuntimeError("listing failed")):
            self.engine.sync(self.account)
        self.assertEqual(len(self.engine.state()["connections"][0]["repositories"]), 2)
        self.refresh("org/two", "org/three")
        offline = self.children()["demo/one"]
        self.assertTrue(offline["unavailable"])
        with self.assertRaises(connectors.Rejected):
            connectors.github_credential(offline)
        self.refresh("demo/one", "org/three")
        restored = self.children()["demo/one"]
        self.assertEqual((restored["id"], restored["cursor"]), (first["id"], 9990))
        self.assertFalse(restored.get("unavailable"))
        self.engine.action(dict(op="disconnect", id=self.account["id"]))
        self.assertEqual(self.engine.db.rows("connections"), [])

    def test_app_repository_connection_can_be_adopted_without_losing_its_cursor(self):
        repo = self.engine.state()["app_repository"]
        with patch("github_signin.active_login", return_value="demo"), patch(
            "connectors.github_request", return_value={"full_name": repo}
        ) as request:
            self.engine.action({"op": "connect_app_repo"})
            self.engine.action({"op": "connect_app_repo"})
            self.assertTrue(all(call.args[1:] == ("GET", "") for call in request.call_args_list))
        (source,) = [c for c in self.engine.state()["connections"] if c["auth_method"] == "gh_repository"]
        with patch("github_signin.token_for", return_value="fixture") as token:
            self.assertEqual(connectors.github_credential(source), "fixture")
            token.assert_called_once_with("demo")
        self.engine.connection_result(source, "Connected", cursor=9990)
        self.refresh(repo)
        self.assertEqual(self.children()[repo]["id"], source["id"])
        self.assertEqual(self.children()[repo]["cursor"], 9990)

    def test_cli_pages_and_independent_issue_and_pr_destinations(self):
        stamp = connectors._iso(9999)

        def api(path, token, params=None, body=None):
            self.assertEqual(token, "fixture")
            if path == "/user":
                return {"login": "demo"}, {}
            if path == "/user/repos":
                repo = "demo/api" if params["page"] == 1 else "org/web"
                return [{"full_name": repo}], (
                    {"Link": '<next>; rel="next"'} if params["page"] == 1 else {}
                )
            if path.endswith("/issues"):
                repo = path.split("/")[2:4]
                item = dict(
                    number=1,
                    state="open",
                    title="Check change",
                    body="Please review",
                    updated_at=stamp,
                    html_url=f"https://github.com/{'/'.join(repo)}/issues/1",
                    user={"login": "teammate"},
                )
                if repo[0] == "org":
                    item["pull_request"] = {}
                return [item], {}
            if "/pulls/" in path:
                return {"requested_reviewers": [{"login": "demo"}]}, {}
            if path.endswith("/issues/comments"):
                return [], {}
            if path.endswith("/issues/1/comments"):
                return {"html_url": "fixture receipt"}, {}
            self.fail("Unexpected GitHub endpoint " + path)

        with (
            patch("github_signin.token_for", return_value="fixture") as cli,
            patch("connectors._gh", side_effect=api) as calls,
        ):
            self.engine.sync(self.account)
            children = self.children()
            for conn in children.values():
                self.engine.sync(conn)
            events = self.engine.db.rows("events")
            self.assertEqual({e["raw"]["destination"] for e in events}, {"demo/api#1", "org/web#1"})
            picked = next(e for e in events if e["raw"]["context"]["is_pull_request"])
            self.assertTrue(picked["raw"]["context"]["review_requested"])
            with self.assertRaises(connectors.Rejected):
                connectors.send(children["demo/api"], picked["raw"], "Approved")
            self.assertEqual(
                connectors.send(children["org/web"], picked["raw"], "Approved"), "fixture receipt"
            )
            self.assertEqual(calls.call_args.args[0], "/repos/org/web/issues/1/comments")
            self.assertTrue(all(c.args == ("demo",) for c in cli.call_args_list))

    def test_legacy_repository_retains_task_and_checkpoint(self):
        legacy = dict(
            id="old",
            config={"repository": "org/old"},
            kind="github",
            identity="Demo",
            auth_method="github_cli",
            label="Old",
            status="Connected",
            cursor=9876,
            revision="initial",
        )
        self.engine.db.put("connections", legacy)
        task = self.engine.new_task(
            "Existing review", "Global", id=digest("old", "item"), github="old", status="review"
        )
        self.refresh("org/old", "org/new")
        self.assertEqual(len(self.engine.state()["connections"]), 1)
        adopted = self.children()["org/old"]
        self.assertEqual((adopted["id"], adopted["cursor"]), ("old", 9876))
        self.assertEqual(self.engine.db.get("tasks", task["id"]), task)

    def test_open_work_browses_and_submits_older_item_once(self):
        self.refresh("org/web")
        row = dict(
            number=5,
            title="Fix my report",
            state="open",
            body="Review data",
            user={"login": "teammate"},
            updated_at=connectors._iso(1),
            assignees=[{"login": "other"}],
            repository_url="https://api.github.com/repos/org/web",
            html_url="https://github.com/org/web/issues/5",
        )
        search = dict(total_count=31, items=[row], incomplete_results=False)
        with (
            patch("github_signin.token_for", return_value="fixture"),
            patch("connectors._gh", return_value=(search, {})) as api,
        ):
            page = self.engine.action(
                dict(op="github_browse", id=self.account["id"], view="reviews", page=1)
            )
            self.assertEqual(api.call_args.args[2]["q"], "is:open is:pr review-requested:demo")
            self.assertTrue(page["more"])
            self.assertEqual(page["items"][0]["repo"], "org/web")
        request = dict(op="github_review_item", id=self.account["id"], repo="org/web", number=5)
        with self.assertRaises(ValueError):
            self.engine.action(dict(request, repo="other/repo"))
        with (
            patch("github_signin.token_for", return_value="fixture"),
            patch("connectors._gh", side_effect=[({"login": "demo"}, {}), (row, {})] * 2),
        ):
            key = self.engine.action(request)["event"]
            self.assertEqual(key, self.engine.action(request)["event"])
        self.engine.discover(key)
        self.assertEqual(self.engine.db.get("tasks", key)["status"], "skipped")
        self.assertEqual(len(self.engine.db.rows("events")), 1)
