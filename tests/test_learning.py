"""Learning from the user's interactions and the learning history. See docs/specs/learning-from-interactions.md."""

import datetime
import json
import tempfile
import unittest

import app_mcp
from engine import Engine


def local(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute).timestamp()


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = local(2026, 11, 20, 10)
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.ctx = self.engine.context
        self.engine.add_project("A", [])
        self.tools = app_mcp.Tools(self.engine)
        self.hermes = app_mcp.Session(app_mcp.ORCHESTRATOR)

    def call(self, session, name, arguments):
        reply = app_mcp.handle(self.tools, session, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        result = reply["result"]
        out = json.loads(result["content"][0]["text"])
        return out, result["isError"] or "refused" in out

    def history(self):
        return [(r["action"], r["author"], r["subject"]) for r in self.db.rows("learnings")]

    def pending(self):
        return [(i["kind"], i["text"]) for i in self.ctx.pending_interactions()]

    def test_each_user_interaction_is_recorded_once(self):
        reply = self.engine.new_task("Reply to Dana", "A", status="review", draft="Hi Dana")
        self.engine.action(dict(op="feedback", id=reply["id"], text="Sign off with just my first name"))
        asked = self.engine.new_task("Pick a date", "A", status="question", question="Friday or Monday?")
        self.engine.action(dict(op="answer", id=asked["id"], text="Monday"))
        spam = self.engine.new_task("Newsletter", "A", status="review")
        self.engine.action(dict(op="dismiss", id=spam["id"]))
        mine = self.engine.new_task("Book flights", "A", status="review")
        self.engine.action(dict(op="close_task", id=mine["id"]))
        self.engine.orchestrator.ask("Remember that Dana is my design lead")
        self.assertEqual(self.pending(), [
            ("correction", "Sign off with just my first name"),
            ("answer", "Asked: Friday or Monday?\nAnswered: Monday"),
            ("dismissed", "Dismissed this task: Newsletter"),
            ("closed", "Closed this task as handled by me: Book flights"),
            ("chat", "Remember that Dana is my design lead"),
        ])
        first = self.ctx.pending_interactions()[0]
        self.assertEqual((first["task"], first["project"]), (reply["id"], "A"))

    def test_hermes_reviews_interactions_only_through_its_session(self):
        task = app_mcp.Session(app_mcp.TASK, "t1")
        self.assertTrue({"interactions_pending", "interactions_reviewed"} <= {t["name"] for t in self.tools.visible(self.hermes)})
        self.assertFalse({"interactions_pending", "interactions_reviewed"} & {t["name"] for t in self.tools.visible(task)})
        _, refused = self.call(task, "interactions_pending", {})
        self.assertTrue(refused)
        t = self.engine.new_task("Reply to Dana", "A", status="review")
        self.engine.action(dict(op="feedback", id=t["id"], text="Use British spelling"))
        self.engine.action(dict(op="dismiss", id=t["id"]))
        listed, _ = self.call(self.hermes, "interactions_pending", {})
        ids = [i["id"] for i in listed["interactions"]]
        self.assertEqual(len(ids), 2)
        # Citing an interaction in a saved lesson marks it reviewed; the other is marked explicitly.
        self.call(self.hermes, "context_save", dict(kind="preference", subject="Spelling", body="British spelling",
                                                    project="A", sources=["interaction:" + ids[0]]))
        self.assertEqual([i["id"] for i in self.ctx.pending_interactions()], [ids[1]])
        self.call(self.hermes, "interactions_reviewed", {"ids": [ids[1]]})
        self.assertEqual(self.ctx.pending_interactions(), [])
        lesson = next(r for r in self.engine.state()["learnings"] if r["action"] == "learned")
        self.assertEqual(lesson["tasks"], [{"id": t["id"], "title": "Reply to Dana"}])

    def test_old_interactions_are_no_longer_offered(self):
        self.ctx.interaction("chat", "Call me Sam")
        self.now += 15 * 86400
        self.assertEqual(self.ctx.pending_interactions(), [])

    def test_history_records_new_and_changed_lessons_only(self):
        save = dict(kind="preference", subject="Sign-off", body="First name only", project="A")
        self.call(self.hermes, "context_save", save)
        self.call(self.hermes, "context_save", save)  # Only refreshes the review date.
        self.call(self.hermes, "context_save", {**save, "body": "First name and a dash"})
        self.assertEqual(self.history(), [("learned", "hermes", "Sign-off"), ("updated", "hermes", "Sign-off")])
        updated = self.db.rows("learnings")[-1]
        self.assertEqual((updated["before"], updated["after"]), ("First name only", "First name and a dash"))

    def test_removed_lesson_is_withheld_blocked_and_restorable(self):
        save = dict(kind="preference", subject="Sign-off", body="First name only", project="A")
        entry, _ = self.call(self.hermes, "context_save", save)
        self.assertEqual(len(self.ctx.for_agent("A", "sign-off")), 1)
        record = self.engine.state()["learnings"][0]
        self.assertTrue(record["in_effect"])
        self.engine.action(dict(op="learning_remove", id=record["id"]))
        self.assertEqual(self.ctx.for_agent("A", "sign-off"), [])
        refused, error = self.call(self.hermes, "context_save", save)
        self.assertTrue(error)
        self.assertIn("removed", json.dumps(refused))
        self.assertFalse(any(r["in_effect"] for r in self.engine.state()["learnings"]))
        with self.assertRaises(ValueError):
            self.engine.action(dict(op="learning_remove", id=record["id"]))
        self.engine.action(dict(op="context_restore", id=entry["id"]))
        _, error = self.call(self.hermes, "context_save", {**save, "body": "First name, no dash"})
        self.assertFalse(error)
        self.assertEqual([a for a, _, _ in self.history()], ["learned", "removed", "restored", "updated"])
        self.assertEqual(self.history()[1][1], "user")

    def test_skill_lessons_are_recorded_and_removable(self):
        self.engine.skill("style", "Lead with the answer", False, "Task agent suggestion")
        record = self.engine.state()["learnings"][0]
        self.assertEqual((record["action"], record["author"], record["target"]), ("proposed", "agent", "skill"))
        self.engine.action(dict(op="learning_remove", id=record["id"]))
        style = next(s for s in self.db.rows("skills") if s["kind"] == "style")
        self.assertEqual((style["active"], style["status"]), (False, "rejected"))
        base = next(s for s in self.db.rows("skills") if s["kind"] == "workflow")
        self.now += 60
        self.engine.skill("workflow", "Also catch 'could you take a look?'", True, "Recovery report")
        revision = self.engine.state()["learnings"][0]
        self.assertEqual(revision["action"], "learned")
        self.engine.action(dict(op="learning_remove", id=revision["id"]))
        self.assertTrue(self.db.get("skills", base["id"])["active"])
        self.assertEqual((self.db.root / "skills/workflow.md").read_text(), base["body"])
        self.assertEqual([a for a, _, _ in self.history()], ["proposed", "removed", "learned", "removed"])

    def test_a_pending_interaction_makes_a_sweep_due(self):
        orch = self.engine.orchestrator
        self.engine.mcp_url = "http://127.0.0.1:9/mcp"
        today = datetime.date.fromtimestamp(self.now).isoformat()
        orch.save(last_sweep=self.now, signature=orch.signature(), last_digest=today)
        self.assertIsNone(orch.due(self.now + 200))
        self.now += 100
        self.ctx.interaction("correction", "Never schedule before 10am")
        self.assertEqual(orch.due(self.now + 100), "sweep")


if __name__ == "__main__":
    unittest.main()
