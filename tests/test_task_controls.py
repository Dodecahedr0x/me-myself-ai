import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import Engine, digest


class TaskControlsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.engine = Engine(self.root / "state")
        self.addCleanup(lambda: self.engine.close())
        self.engine.add_project("Group", [])
        self.repo = self.root / "client"
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin",
                        "https://github.com/fixture/client.git"], check=True)
        self.engine.add_project("Child", [str(self.repo)], "Group")
        self.conn = dict(id="fixture", kind="github", label="Fixture", config={"repository": "Fixture/Client"})
        self.engine.db.put("connections", self.conn)
        self.raw = dict(key="issue:1", title="Update help", body="Improve the help page.",
                        context={"repository":"fixture/client"}, account="user",
                        destination="fixture/client#1", timestamp=self.engine.now())
        self.key = digest(self.conn["id"], self.raw["key"])

    def ignore(self, projects=(), repos=()):
        self.engine.action(dict(op="ignorelist", projects=list(projects), repositories=list(repos)))

    def test_manual_folder_check_verifies_registered_checkout_before_waking_recovery(self):
        self.engine.repository_recheck.clear()
        self.engine.wake.clear()
        original = self.engine.db.rows("projects")
        for path in (str(self.root), str(self.repo / "subdirectory")):
            with self.assertRaisesRegex(ValueError, "registered"):
                self.engine.action(dict(op="check_folder_access", path=path))
        with patch("worker._git", side_effect=RuntimeError("Access denied")), self.assertRaisesRegex(RuntimeError, "denied"):
            self.engine.action(dict(op="check_folder_access", path=str(self.repo.resolve())))
        self.assertFalse(self.engine.repository_recheck.is_set())
        self.engine.action(dict(op="check_folder_access", path=str(self.repo.resolve())))
        self.assertTrue(self.engine.repository_recheck.is_set())
        self.assertTrue(self.engine.wake.is_set())
        self.assertEqual(self.engine.db.rows("projects"), original)

    def test_contextual_exclusion_changes_only_its_target(self):
        self.ignore(repos=["elsewhere/keep-ignored"])
        task = self.engine.new_task("Prepare release", "Child")
        repository_task = self.engine.new_task("Check CI", "Global", github_repository="Fixture/Client")
        def set_exclusion(scope, value, ignored):
            self.engine.action(dict(op="set_exclusion", scope=scope, value=value, ignored=ignored))
        set_exclusion("project", "Group", True)
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "paused")
        set_exclusion("repository", "Fixture/Client", True)
        set_exclusion("project", "Group", False)
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "queued")
        self.assertEqual(self.engine.db.get("tasks", repository_task["id"])["status"], "paused")
        set_exclusion("repository", "FIXTURE/CLIENT", False)
        self.assertEqual(self.engine.db.get("tasks", repository_task["id"])["status"], "queued")
        with self.assertRaises(ValueError):
            set_exclusion("project", "Global", True)
        self.assertEqual(self.engine.scope.config["repositories"], ["elsewhere/keep-ignored"])

    def test_project_inheritance_prevents_reads_discovery_and_stale_approval(self):
        self.engine.ingest(self.conn, self.raw)
        self.engine.new_task("Update help", "Global", event=self.key, id=self.key, status="review",
                             draft="publish", account="user", destination="fixture/client#1")
        version = self.engine.db.get("tasks", self.key)["version"]
        self.ignore(["Group"])
        with patch("connectors.poll") as poll, patch("worker.run_agent") as agent:
            self.engine.sync(self.conn)
            self.engine.discover(self.key)
            self.engine.process(self.key)
            poll.assert_not_called()
            agent.assert_not_called()
        self.assertEqual(self.engine.repositories("Global"), [])
        self.assertEqual(self.engine.db.get("tasks", self.key)["status"], "paused")
        with self.assertRaises(ValueError):
            self.engine.deliver(self.key, version)
        self.engine.close()
        self.engine = Engine(self.root / "state")
        self.assertEqual(self.engine.state()["ignorelist"]["projects"], ["Group"])
        self.ignore()
        self.assertEqual(self.engine.db.get("tasks", self.key)["status"], "queued")
        self.assertEqual(self.engine.db.get("tasks", self.key)["draft"], "")

    def test_slug_excludes_only_matching_repo_and_suggestions(self):
        self.engine.ingest(self.conn, self.raw)
        self.ignore(repos=["FIXTURE/client"])
        self.assertFalse(self.engine.suggestions.gather(self.engine.suggestions.config(), self.engine.now())[0])
        self.assertEqual(self.engine.new_task("Fix fixture/client#2", "Global")["status"], "paused")
        self.assertEqual(self.engine.new_task("Fix fixture/client-tools#2", "Global")["status"], "queued")
        self.assertFalse(self.engine.repository_candidates({"project": "Global"}, self.conn))
        self.ignore(repos=[str(self.repo)])
        self.assertTrue(self.engine.scope.connection(self.conn))
        with self.assertRaises(ValueError):
            self.ignore(repos=["typo"])
        self.assertTrue(self.engine.scope.connection(self.conn))

    def test_closing_during_inference_survives_source_updates(self):
        self.engine.ingest(self.conn, self.raw)
        self.engine.new_task("Help", "Child", id=self.key, event=self.key)
        def close_during_run(*args):
            self.engine.action(dict(op="close_task", id=self.key))
            return dict(outcome="question", text="Proceed?", independent_steps=[dict(title="Extra", body="Work")])
        with patch("worker.run_agent", side_effect=close_during_run) as agent:
            self.engine.process(self.key)
            self.engine.ingest(self.conn, dict(self.raw, body="Updated"))
            self.engine.discover(self.key)
            agent.assert_called_once()
        rows = self.engine.db.rows("tasks")
        self.assertEqual([(r["id"], r["status"]) for r in rows], [(self.key, "closed")])
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="retry", id=self.key))

    def test_uncertain_delivery_stays_reconcilable(self):
        task = self.engine.new_task("Check delivery", "Child", status="uncertain", draft="Exact preview")
        self.ignore(["Group"])
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="close_task", id=task["id"]))
        self.engine.action(dict(op="reconcile", id=task["id"], version=task["version"], delivered=False))
        self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "paused")

    def test_ignore_during_preparation_discards_result_and_independent_steps(self):
        task = self.engine.new_task("Prepare response", "Child")
        def exclude_during_run(*args):
            self.ignore(["Group"])
            return dict(outcome="question", text="Proceed?", independent_steps=[dict(title="Extra", body="Work")])
        with patch("worker.run_agent", side_effect=exclude_during_run):
            self.engine.process(task["id"])
        tasks = self.engine.db.rows("tasks")
        self.assertEqual([(t["id"], t["status"]) for t in tasks], [(task["id"], "paused")])

    def test_watching_parent_folder_omits_ignored_checkout(self):
        (self.root / "notes.txt").write_text("shared notes")
        (self.repo / "notes.txt").write_text("repo notes")
        self.engine.suggestions.configure({"folders": [str(self.root)], "foreground": False})
        self.ignore(repos=["https://github.com/fixture/client"])
        rows, gaps = self.engine.suggestions.gather(self.engine.suggestions.config(), self.engine.now())
        self.assertFalse(gaps)
        self.assertEqual([r["title"] for r in rows.values()], ["notes.txt"])
