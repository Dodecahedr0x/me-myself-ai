import concurrent.futures
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import suggestions
import worker
from engine import Engine


class ActivitySuggestions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.folder = self.root / "Budget"
        self.folder.mkdir()
        self.now = 1800000000
        self.engine = Engine(self.root / "workspace", lambda: self.now)
        self.addCleanup(self.engine.close)
        self.service = self.engine.suggestions
        self.db = self.engine.db
        self.engine.action(dict(op="suggestion_config", folders=[str(self.folder)], foreground=False))
        self.conn = dict(
            id="slack", kind="slack", status="Connected", label="Finance", config={"channel": "C1"}
        )
        self.db.put("connections", self.conn)

    def write(self, name, at=None):
        path = self.folder / name
        path.parent.mkdir(exist_ok=True, parents=True)
        path.write_text("fixture contents should never enter observations")
        when = self.now if at is None else at
        os.utime(path, (when, when))
        return path

    def event(self, key="request", owned=False):
        self.engine.ingest(
            self.conn,
            dict(
                key=key,
                timestamp=self.now,
                title="Budget requested",
                body="Can you send the revised budget?",
                owned=owned,
                authored=False,
                account="me",
                destination="C1 / thread 1.0",
                context={},
                url="https://example.test/request",
            ),
        )

    def propose(self, role, context, *args):
        self.assertEqual(role, "suggestions")
        rows = context["evidence"]
        anchor = next(r for r in rows if r["source"] != "Mac app")
        return dict(
            suggestions=[
                dict(
                    title="Prepare the updated budget",
                    body="Compare the revised budget with the request.",
                    reason="The budget was revised after the finance request.",
                    project="Unknown",
                    anchor=anchor["id"],
                    evidence=[r["id"] for r in rows],
                )
            ]
        )

    def test_correlates_and_starts_once_with_original_destination(self):
        self.event()
        self.write("estimate.xlsx", self.now - 10)
        with patch.object(self.engine, "infer", side_effect=self.propose) as infer:
            self.service.run()
            self.service.run()
            infer.assert_called_once()
        (s,) = self.service.state()["pending"]
        self.assertEqual({r["source"] for r in s["evidence"]}, {"File", "slack"})
        connected = next(r for r in s["evidence"] if r["source"] == "slack")
        self.assertFalse(connected["authored_by_user"])
        self.assertEqual(connected["destination"], "C1 / thread 1.0")
        self.assertNotIn("fixture contents", str(s["evidence"]))

        def start(_):
            try:
                self.engine.action(dict(op="suggestion_start", id=s["id"]))
                return True
            except ValueError:
                return False

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(start, [1, 2])), [False, True])
        (t,) = self.db.rows("tasks")
        self.assertEqual(
            (t["event"], t["account"], t["destination"], t["project"]),
            (s["anchor"], "me", "C1 / thread 1.0", "Global"),
        )

    def test_files_only_dismissal_survives_restart_and_observes_window(self):
        self.write("draft.md")
        self.write("old.txt", self.now - 10801)
        self.write("build/generated.py")
        self.write(".hidden/file")
        (self.folder / "linked").symlink_to(self.root / "workspace", target_is_directory=True)
        with patch.object(self.engine, "infer", side_effect=self.propose):
            self.service.run()
        (s,) = self.service.state()["pending"]
        self.assertEqual([r["title"] for r in s["evidence"]], ["draft.md"])
        self.service.decide(s["id"], False)
        other = Engine(self.root / "workspace", lambda: self.now)
        self.addCleanup(other.close)
        with patch.object(other, "infer") as infer:
            other.suggestions.run()
            infer.assert_not_called()
        self.assertEqual(other.suggestions.state()["pending"], [])

    def test_rejects_invented_evidence_and_owned_activity(self):
        self.event(owned=True)
        self.write("todo.md")

        def invalid(role, payload, *args):
            self.assertEqual({r["source"] for r in payload["evidence"]}, {"File"})
            out = self.propose(role, payload, *args)
            out["suggestions"][0]["evidence"] = ["invented"]
            return out

        with patch.object(self.engine, "infer", side_effect=invalid):
            self.service.run()
        self.assertEqual(self.service.state()["pending"], [])

    def test_source_reconfiguration_invalidates_inflight_inference(self):
        self.write("work.txt")

        def reconfigure(role, payload, *args):
            self.service.configure(dict(folders=[], foreground=False))
            return self.propose(role, payload, *args)

        with patch.object(self.engine, "infer", side_effect=reconfigure):
            self.service.run()
        self.assertEqual(self.service.state()["pending"], [])

    def test_frontmost_name_only_is_insufficient_and_disable_purges_samples(self):
        self.service.configure(dict(folders=[], foreground=True))
        with (
            patch("suggestions.foreground_app", return_value="Numbers"),
            patch.object(self.engine, "infer") as infer,
        ):
            self.service.run()
            infer.assert_not_called()
        self.assertEqual([r["title"] for r in self.db.rows("app_observations")], ["Numbers"])
        self.service.configure(dict(folders=[], foreground=False))
        self.assertFalse(self.db.rows("app_observations"))

    def test_git_matches_local_identity(self):
        def git(*args):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.folder),
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    *args,
                ],
                env={
                    **os.environ,
                    "GIT_AUTHOR_DATE": f"@{self.now} +0000",
                    "GIT_COMMITTER_DATE": f"@{self.now} +0000",
                },
                check=True,
                capture_output=True,
            )

        git("init", "-q")
        git("config", "user.email", "user@example.test")
        git("config", "user.name", "Tester")
        git("commit", "--allow-empty", "-m", "Prepare release figures")
        git("commit", "--allow-empty", "--author=Other <other@example.test>", "-m", "Someone else's work")
        rows = suggestions.recent_commits(self.folder, self.now - 10800, self.now)
        self.assertEqual([r["title"] for r in rows], ["Prepare release figures"])

    def test_worker_schema_allows_no_suggestions(self):
        self.assertEqual(worker._validate("suggestions", {"suggestions": []}), {"suggestions": []})
        with self.assertRaises(worker.WorkerError):
            worker._validate("suggestions", {"suggestions": [{"title": "Unknown"}]})
