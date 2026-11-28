"""Desktop notifications: which task states notify, and that none repeats. See docs/specs/dashboard.md#notifications."""

import json
from pathlib import Path
import tempfile
import unittest

from engine import Engine
from notifications import Notifier


def task(ident, status, version=1, **fields):
    return dict(id=ident, title="Task " + ident, status=status, version=version, created=1, **fields)


class NotifierTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "notifications.json"
        self.posted = []
        self.enabled = True

    def notifier(self):
        return Notifier(self.path, self.posted.append, lambda: self.enabled)

    def test_first_run_records_current_states_without_notifying(self):
        n = self.notifier()
        self.assertEqual(n.check([task("a", "review"), task("b", "failed")]), [])
        self.assertEqual(self.posted, [])
        self.assertEqual(json.loads(self.path.read_text()), {"a": "review", "b": "failed"})

    def test_each_state_that_needs_the_user_notifies_once(self):
        n = self.notifier()
        n.check([])
        tasks = [task("r", "review"), task("q", "question"), task("f", "question", checkout_inaccessible=True),
                 task("x", "failed"), task("u", "uncertain"), task("w", "working"), task("d", "done")]
        titles = {item["task"]: item["title"] for item in n.check(tasks)}
        self.assertEqual(titles, {"r": "Needs your approval", "q": "Needs your answer", "f": "Needs folder access",
                                  "x": "Failed: retry or give instructions", "u": "Check whether it was delivered"})
        self.assertEqual(self.posted[0], dict(task="r", key="r:1", title="Needs your approval", body="Task r"))
        self.assertEqual(n.check(tasks), [])  # A refresh with the same states posts nothing.
        self.assertEqual(len(self.posted), 5)

    def test_new_version_in_the_same_state_does_not_repeat(self):
        n = self.notifier()
        n.check([task("a", "queued")])
        self.assertEqual(len(n.check([task("a", "review", 2)])), 1)
        self.assertEqual(n.check([task("a", "review", 3)]), [])  # The user edited the draft.

    def test_returning_to_a_state_after_leaving_it_notifies_again(self):
        n = self.notifier()
        n.check([task("a", "queued")])
        n.check([task("a", "review", 2)])
        n.check([task("a", "queued", 3)])  # Sent back with instructions.
        self.assertEqual([i["key"] for i in n.check([task("a", "review", 4)])], ["a:4"])

    def test_folder_access_is_a_change_from_a_plain_question(self):
        n = self.notifier()
        n.check([task("a", "question")])
        self.assertEqual([i["title"] for i in n.check([task("a", "question", 2, checkout_inaccessible=True)])],
                         ["Needs folder access"])

    def test_restart_does_not_repeat_and_catches_changes_made_while_closed(self):
        self.notifier().check([])
        self.notifier().check([task("a", "review")])
        restarted = self.notifier()
        self.assertEqual(restarted.check([task("a", "review")]), [])
        self.assertEqual([i["task"] for i in self.notifier().check([task("a", "review"), task("b", "failed")])], ["b"])
        self.assertEqual(len(self.posted), 2)

    def test_only_tasks_the_orchestrator_dispatched_notify_when_done(self):
        n = self.notifier()
        n.check([task("h", "working", origin="orchestrator"), task("m", "working")])
        out = n.check([task("h", "done", 2, origin="orchestrator"), task("m", "done", 2),
                       task("s", "skipped", origin="orchestrator")])
        self.assertEqual([(i["task"], i["title"]) for i in out], [("h", "Finished")])

    def test_turned_off_posts_nothing_and_turning_on_does_not_replay(self):
        n = self.notifier()
        n.check([])
        self.enabled = False
        self.assertEqual(n.check([task("a", "review")]), [])
        self.enabled = True
        self.assertEqual(n.check([task("a", "review")]), [])
        self.assertEqual(len(n.check([task("a", "review"), task("b", "failed")])), 1)

    def test_without_a_desktop_host_nothing_is_tracked(self):
        n = Notifier(self.path)
        self.assertEqual(n.check([task("a", "review")]), [])
        self.assertFalse(self.path.exists())

    def test_unwritable_record_still_prevents_repeats_in_this_session(self):
        self.path.mkdir()  # Writing the record fails.
        n = self.notifier()
        n.check([task("a", "queued")])
        self.assertEqual(len(n.check([task("a", "review", 2)])), 1)
        self.assertEqual(n.check([task("a", "review", 2)]), [])


class EngineNotificationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.engine = Engine(Path(temp.name) / "state")
        self.addCleanup(self.engine.close)
        self.posted = []
        self.engine.notifier.sink = self.posted.append

    def test_engine_tasks_notify_and_the_setting_turns_it_off(self):
        self.engine.notifier.check(self.engine.db.rows("tasks"))
        t = self.engine.new_task("Reply to Ana", "Global", status="review", draft="Hi")
        self.engine.notifier.check(self.engine.db.rows("tasks"))
        self.assertEqual(self.posted, [dict(task=t["id"], key=f"{t['id']}:{t['version']}",
                                            title="Needs your approval", body="Reply to Ana")])
        self.engine.action(dict(op="settings", notifications=False))
        self.assertIs(self.engine.settings()["notifications"], False)
        self.engine.new_task("Fix build", "Global", status="failed")
        self.engine.notifier.check(self.engine.db.rows("tasks"))
        self.assertEqual(len(self.posted), 1)
        with self.assertRaisesRegex(ValueError, "notification"):
            self.engine.action(dict(op="settings", notifications="yes"))


if __name__ == "__main__":
    unittest.main()
