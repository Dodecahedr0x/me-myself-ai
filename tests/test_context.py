"""Context store, schedules, the Hermes bridge and handoff evidence. See docs/specs/context-and-schedules.md."""

import datetime
import json
import tempfile
import unittest
from unittest.mock import patch

from context import DAY, next_run
from engine import Engine, digest
import app_mcp


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

        def model(role, payload, provider, *rest, **options):
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

        def model(role, payload, provider, *rest, **options):
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


class OrchestratorContextToolTests(unittest.TestCase):
    """Hermes reaches context and schedules only through the app MCP server's orchestrator session."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = local(2026, 11, 20, 10)
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.tools = app_mcp.Tools(self.engine)

    def call(self, session, name, arguments):
        reply = app_mcp.handle(self.tools, session, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        result = reply["result"]
        return json.loads(result["content"][0]["text"]), result["isError"]

    def test_only_the_orchestrator_curates_context_and_schedules(self):
        hermes = app_mcp.Session(app_mcp.ORCHESTRATOR)
        task = app_mcp.Session(app_mcp.TASK, "t1")
        names = {t["name"] for t in self.tools.visible(hermes)}
        self.assertTrue({"context_save", "context_retire", "context_stale", "schedule_create", "schedule_cancel"} <= names)
        self.assertFalse(names & {"send_email", "send_message", "comment"})
        self.assertFalse({t["name"] for t in self.tools.visible(task)} & set(app_mcp.CONTEXT_TOOLS))
        saved, error = self.call(hermes, "context_save", {"kind": "person", "subject": "Dana", "body": "Design lead"})
        self.assertFalse(error, saved)
        found, _ = self.call(hermes, "context_search", {"query": "design"})
        self.assertEqual([e["subject"] for e in found["entries"]], ["Dana"])
        refused, error = self.call(task, "context_save", {"kind": "fact", "subject": "x", "body": "y"})
        self.assertTrue(error)
        bad, error = self.call(hermes, "schedule_create", {"title": "x", "body": "y", "origin": "user", "reason": "r", "time": "25:00"})
        self.assertTrue(error)
        self.assertIn("HH:MM", bad["refused"])
        self.assertGreater(self.engine.state()["hermes"]["last_call"], 0)

    def test_cleanup_runs_daily_only_when_context_is_stale(self):
        orch = self.engine.orchestrator
        self.engine.mcp_url = "http://127.0.0.1:1/mcp"
        orch.save(last_sweep=self.now, signature=orch.signature(), last_digest="2026-11-20")
        self.assertIsNone(orch.due(self.now))
        self.engine.context.save(dict(kind="fact", subject="Office", body="Closed Friday", review_after_days=1))
        self.now += 2 * DAY
        orch.save(last_sweep=self.now, signature=orch.signature(), last_digest="2026-11-22")
        self.assertEqual(orch.due(self.now), "cleanup")


if __name__ == "__main__":
    unittest.main()
