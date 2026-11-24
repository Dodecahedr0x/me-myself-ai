"""Context store, schedules, the Hermes bridge and handoff evidence. See docs/specs/context-and-schedules.md."""

import datetime
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from context import DAY, next_run
from engine import Engine, digest
import hermes_mcp
import server

ROOT = Path(__file__).resolve().parent.parent


def local(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute).timestamp()


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = local(2026, 11, 20, 10)  # A Friday
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.ctx = self.engine.context
        self.engine.add_project("Parent", [])
        self.engine.add_project("A", [], "Parent")
        self.engine.add_project("B", [], "Parent")

    def save(self, subject, project, **extra):
        return self.ctx.call("context_save", dict(kind="fact", subject=subject, body=subject + " details", project=project, **extra))

    def test_save_updates_the_same_entry(self):
        first = self.save("Release train", "A")
        self.now += DAY
        second = self.ctx.call("context_save", dict(kind="fact", subject="release train", body="Moved to Tuesdays", project="A"))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.db.rows("context")), 1)
        self.assertEqual(self.db.get("context", first["id"])["body"], "Moved to Tuesdays")
        with self.assertRaises(ValueError):
            self.ctx.call("context_save", dict(kind="gossip", subject="x", body="y"))

    def test_agents_get_only_scoped_fresh_context_and_the_log_names_it(self):
        sibling = self.save("Alpha budget", "A")
        own = self.save("Bravo budget", "B")
        parent = self.save("Parent budget", "Parent")
        workspace = self.save("Workspace budget", "Global")
        stale = self.save("Old budget", "B", review_after_days=1)
        self.now += 2 * DAY
        for entry in (own, parent, workspace):  # Refreshed by Hermes; the stale one is not.
            self.save(entry["subject"], entry["project"])
        seen = {}

        def model(role, payload, provider):
            seen.update(payload)
            return dict(outcome="done", kind="task", text="ok", commitments=[])

        task = self.engine.new_task("Summarize the budget", "B")
        with patch("engine.worker.run_agent", side_effect=model):
            self.engine.process(task["id"])
        subjects = {e["subject"] for e in seen["context"]}
        self.assertEqual(subjects, {"Bravo budget", "Parent budget", "Workspace budget"})
        self.assertNotIn(sibling["subject"], subjects)
        self.assertNotIn(stale["subject"], subjects)
        supplied = next(a for a in self.db.history() if a["kind"] == "Context supplied")
        self.assertIn(own["id"], supplied["detail"])
        self.assertNotIn(sibling["id"], supplied["detail"])
        self.assertEqual([e["id"] for e in self.ctx.call("context_stale", {})["entries"]], [stale["id"]])
        self.save("Old budget", "B")
        self.assertEqual(self.ctx.call("context_stale", {})["entries"], [])

    def test_retired_context_is_withheld_and_can_be_restored(self):
        entry = self.save("Vendor", "Global")
        self.engine.action(dict(op="context_retire", id=entry["id"]))
        self.assertEqual(self.ctx.for_agent("Global", "vendor"), [])
        self.engine.action(dict(op="context_restore", id=entry["id"]))
        self.assertEqual(len(self.ctx.for_agent("Global", "vendor")), 1)

    def test_weekday_schedule_runs_monday_and_never_piles_up(self):
        s = self.ctx.call("schedule_create", dict(
            title="Open PR summary", body="Summarize open pull requests", project="A",
            time="09:00", days=[0, 1, 2, 3, 4], origin="user", reason="Asked in a task",
        ))
        self.assertEqual(s["next"], local(2026, 11, 23, 9))
        self.engine.tick()
        self.assertFalse([t for t in self.db.rows("tasks") if t["source"] == "Schedule"])
        self.now = local(2026, 11, 23, 9, 1)
        with patch.object(self.engine, "process"):  # Keep the run open.
            self.engine.tick()
        runs = [t for t in self.db.rows("tasks") if t.get("schedule") == s["id"]]
        self.assertEqual(len(runs), 1)
        self.assertEqual(self.db.get("schedules", s["id"])["next"], local(2026, 11, 24, 9))
        self.now = local(2026, 11, 26, 9, 1)  # Missed two occurrences while closed; one check.
        self.ctx.due()
        self.assertEqual(len([t for t in self.db.rows("tasks") if t.get("schedule") == s["id"]]), 1)
        self.assertEqual(self.db.get("schedules", s["id"])["next"], local(2026, 11, 27, 9))
        self.assertTrue(any(a["kind"] == "Schedule skipped" for a in self.db.history()))
        runs[0]["status"] = "done"
        self.db.put("tasks", runs[0])
        self.now = local(2026, 11, 27, 9, 1)
        self.ctx.due()
        self.assertEqual(len([t for t in self.db.rows("tasks") if t.get("schedule") == s["id"]]), 2)

    def test_one_time_schedule_finishes(self):
        s = self.ctx.call("schedule_create", dict(
            title="Chase invoice", body="Check the invoice was paid", once_at=self.now + 60,
            origin="inferred", reason="Promised in Friday's email",
        ))
        self.assertEqual(next_run(s, self.now + 60), None)
        self.now += 61
        self.ctx.due()
        self.assertEqual(self.db.get("schedules", s["id"])["status"], "done")

    def test_user_cancellation_sticks_and_hermes_cancellation_is_logged(self):
        spec = dict(title="Weekly digest", body="Digest", project="A", time="08:00", origin="inferred", reason="Pattern")
        s = self.ctx.call("schedule_create", spec)
        self.engine.action(dict(op="schedule_cancel", id=s["id"]))
        with self.assertRaisesRegex(ValueError, "user cancelled"):
            self.ctx.call("schedule_create", spec)
        other = self.ctx.call("schedule_create", {**spec, "title": "Standup notes"})
        self.ctx.call("schedule_cancel", dict(id=other["id"], reason="The standup was retired"))
        log = next(a for a in self.db.history() if a["kind"] == "Hermes cancelled schedule")
        self.assertIn("The standup was retired", log["detail"])
        self.assertEqual(self.engine.state()["schedules"][0]["status"], "cancelled")

    def test_handoff_candidates_need_five_uncorrected_deliveries(self):
        tasks = [self.engine.new_task(f"Status {i}", "A", kind="status", receipts=["sent"], status="done") for i in range(5)]
        self.assertEqual(self.ctx.handoffs(), [dict(project="A", kind="status", delivered=5, corrected=0)])
        tasks[0]["feedback"].append({"text": "Shorter please"})
        self.db.put("tasks", tasks[0])
        self.assertEqual(self.ctx.handoffs(), [])
        tasks[0]["feedback"] = []
        self.db.put("tasks", tasks[0])
        self.engine.action(dict(op="rule", project="A", kind="status", mode="auto"))
        self.assertEqual(self.ctx.handoffs(), [])

    def test_failed_skill_check_keeps_the_current_revision(self):
        conn = dict(id="sourceA", kind="slack", label="A", config={}, status="Connected")
        self.db.put("connections", conn)
        accepted = dict(key="old", timestamp=self.now - 5 * DAY, title="Accepted request", body="Please review",
                        destination="d", account="me", authored=False, owned=False, context={})
        self.engine.ingest(conn, accepted)
        event = digest(conn["id"], "old")
        self.engine.new_task("Accepted", "A", id=event, event=event, status="done")
        before = (self.db.root / "skills/workflow.md").read_text()
        report = self.engine.new_task("Missed work", "Global", "Check indirect asks", special="report")

        def poll(c, start, stop):
            return [dict(accepted, key="new", timestamp=start + 1, title="Missed request")]

        def model(role, payload, provider):
            if role == "improvement":
                return dict(body="Only flag direct mentions.", reason="Narrower")
            if role == "decision":
                return dict(question=payload["question"], kind="improvement")
            relevant = "previously accepted" not in payload.get("instruction", "")
            return dict(title="t", relevant=relevant, owned=False, kind="reply", reason="checked", commitments=[])

        with patch("engine.connectors.poll", side_effect=poll), patch("engine.worker.run_agent", side_effect=model):
            self.engine.process(report["id"])
        self.assertEqual((self.db.root / "skills/workflow.md").read_text(), before)
        failed = next(s for s in self.db.rows("skills") if s["status"] == "failed check")
        self.assertFalse(failed["active"])
        self.assertIn("Accepted request", failed["reason"])
        self.assertEqual(self.db.get("tasks", report["id"])["status"], "question")


class HermesBridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)

    def test_tool_endpoint_requires_token_host_and_no_browser_origin(self):
        engine = Engine(self.data)
        self.addCleanup(engine.close)
        http = server.HTTP(engine)
        self.addCleanup(http.server_close)
        good = {"Host": f"127.0.0.1:{http.server_port}", "Authorization": "Bearer " + engine.context.token()}
        for headers, expected in [
            ({**good, "Authorization": "Bearer wrong"}, 403),
            ({**good, "Host": "example.net"}, 403),
            ({**good, "Origin": "https://example.net"}, 403),
            (good, 200),
        ]:
            body = json.dumps({"name": "schedule_list", "arguments": {}}).encode()
            req = server.Handler.__new__(server.Handler)
            req.server, req.path, req.rfile = http, "/hermes/tool", BytesIO(body)
            req.headers = {**headers, "Content-Length": str(len(body))}
            calls = []
            req.output = lambda status, *rest, **kw: calls.append(status)
            req.do_POST()
            self.assertEqual(calls, [expected])

    def test_stdio_bridge_lists_tools_and_reports_a_closed_app(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "schedule_list", "arguments": {}}},
        ]
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "hermes_mcp.py"), "--data", str(self.data)],
            input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True, timeout=30,
        )
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("context_save", {t["name"] for t in replies[1]["result"]["tools"]})
        self.assertTrue(replies[2]["result"]["isError"])
        self.assertIn("not running", replies[2]["result"]["content"][0]["text"])

    def test_bridge_reaches_a_running_app(self):
        engine = Engine(self.data)
        self.addCleanup(engine.close)
        http = server.HTTP(engine)
        self.addCleanup(http.server_close)
        engine.context.publish(http.server_port)
        self.assertEqual((self.data / "hermes.json").stat().st_mode & 0o777, 0o600)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        self.addCleanup(http.shutdown)
        call = lambda name, args: hermes_mcp.handle(  # noqa: E731
            self.data, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
        )["result"]
        saved = call("context_save", {"kind": "person", "subject": "Dana", "body": "Design lead"})
        self.assertFalse(saved["isError"], saved)
        found = json.loads(call("context_search", {"query": "design"})["content"][0]["text"])
        self.assertEqual([e["subject"] for e in found["entries"]], ["Dana"])
        bad = call("schedule_create", {"title": "x", "body": "y", "origin": "user", "reason": "r", "time": "25:00"})
        self.assertTrue(bad["isError"])
        self.assertGreater(engine.state()["hermes"]["last_call"], 0)


if __name__ == "__main__":
    unittest.main()
