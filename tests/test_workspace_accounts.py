import json
import tempfile
import unittest
from unittest.mock import patch

import connectors
import google_workspace
from engine import Engine


class WorkspaceAccounts(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.now = 1800000000
        self.engine = Engine(folder.name, clock=lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db

    def google(self, address, services):
        key = address
        c = dict(
            id=key,
            revision="1",
            identity=address,
            kind="email",
            label=address,
            auth_method="google",
            google_services=services,
            config=connectors.validate(
                "email", dict(address=address, imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
            ),
            next=0,
            status="Connected",
        )
        self.db.put("connections", c)
        return c

    def test_workspace_tracks_changes_without_selection(self):
        service = self.engine.signins
        channels = [dict(value=i, label="#" + i) for i in ["C1", "C2"]]
        grant = dict(
            account="User (workspace)",
            identity="T1/U1",
            options=channels,
            credential=dict(provider="slack", access_token="fixture"),
        )
        keys = {}
        with (
            patch("connectors.store_secret", side_effect=keys.__setitem__),
            patch("connectors._secret", side_effect=keys.__getitem__),
            patch("connectors.delete_secret", side_effect=keys.__delitem__),
        ):
            service.save_slack_channels(dict(previous=None), grant, None)
            (parent,) = self.engine.state()["connections"]
            self.assertEqual(parent["channel_count"], 2)
            children = {
                c["config"]["channel"]: c for c in self.db.rows("connections") if c.get("slack_parent")
            }
            with patch(
                "account_oauth._slack_choices",
                return_value=("User", "T1/U1", [channels[1], dict(value="C3", label="#new")]),
            ):
                service.refresh_workspace(parent)
            allrows = {
                c["config"]["channel"]: c for c in self.db.rows("connections") if c.get("slack_parent")
            }
            self.assertTrue(allrows["C1"]["unavailable"])
            self.assertEqual(children["C2"]["id"], allrows["C2"]["id"])
            self.assertIn("C3", allrows)
            with (
                patch("account_oauth._slack_choices", side_effect=ValueError("network")),
                self.assertRaisesRegex(ValueError, "network"),
            ):
                service.refresh_workspace(parent)
            self.assertFalse(self.db.get("connections", allrows["C2"]["id"]).get("unavailable"))
            self.engine.action(dict(op="disconnect", id=parent["id"]))
        self.assertFalse(self.db.rows("connections"))
        self.assertFalse(keys)

    def test_deadline_requires_approval_and_records_calendar_receipt(self):
        owner = self.google("work@example.test", ["Calendar"])
        self.engine.followups(
            "source", "Global", [{"title": "Submit report", "due": self.now + 1000}], "team"
        )
        (t,) = [t for t in self.db.rows("tasks") if t["kind"] == "calendar"]
        self.engine.process(t["id"])
        t = self.db.get("tasks", t["id"])
        self.assertEqual(t["status"], "review")
        self.assertEqual(t["account"], owner["identity"])
        body = json.loads(t["draft"])
        self.assertEqual(body["summary"], "Submit report")
        with (
            patch("connectors._transport_secret", return_value="token"),
            patch(
                "connectors._http",
                return_value=(dict(id=body["id"], htmlLink="https://example.test/event"), {}),
            ) as api,
        ):
            with self.assertRaises(ValueError):
                self.engine.deliver(t["id"], t["version"], automatic=True)
            api.assert_not_called()
            self.engine.action(dict(op="approve", id=t["id"], version=t["version"]))
            with self.assertRaises(ValueError):
                self.engine.action(dict(op="approve", id=t["id"], version=t["version"]))
            api.assert_called_once()
            self.assertEqual(api.call_args.kwargs["body"], body)
        done = self.db.get("tasks", t["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["receipts"][0]["connection"], owner["id"])

    def test_account_choice_when_several_calendars_connected(self):
        for email in ["one@example.test", "two@example.test"]:
            self.google(email, ["Calendar"])
        t = self.engine.new_task(
            "Calendar booking",
            "Global",
            calendar_details=dict(title="Due", start=self.now + 300, end=self.now + 600),
        )
        with patch.object(self.engine, "infer", return_value=dict(question="Which calendar?")):
            self.engine.process(t["id"])
        self.assertEqual(self.db.get("tasks", t["id"])["status"], "question")
        self.engine.action(dict(op="answer", id=t["id"], text="two@example.test"))
        self.engine.process(t["id"])
        self.assertEqual(self.db.get("tasks", t["id"])["account"], "two@example.test")

    def test_conflict_checks_exact_previously_created_event(self):
        conn = self.google("work@example.test", ["Calendar"])
        body = google_workspace.calendar_payload("t", "Submit report", self.now, self.now + 900)
        task = dict(
            draft=json.dumps(body),
            calendar_payload=body,
            account=conn["identity"],
            destination="Primary calendar",
        )
        with (
            patch("connectors._transport_secret", return_value="token"),
            patch("connectors._http", side_effect=[connectors.Rejected("HTTP 409"), (body, {})]),
        ):
            self.assertIn(body["id"], google_workspace.create_event(conn, task))
        with (
            patch("connectors._transport_secret", return_value="token"),
            patch(
                "connectors._http",
                side_effect=[connectors.Rejected("HTTP 409"), (dict(body, summary="Different"), {})],
            ),
            self.assertRaises(connectors.Rejected),
        ):
            google_workspace.create_event(conn, task)

    def test_partial_google_consent_and_independent_sources(self):
        c = self.google("work@example.test", ["Calendar", "Drive"])
        row = dict(
            id="doc",
            name="Plan",
            modifiedTime=google_workspace.utc(self.now),
            mimeType="application/vnd.google-apps.document",
        )

        def api(url, token, params):
            if "calendar" in url:
                raise ValueError("Calendar offline")
            return {"files": [row]}, {}

        with (
            patch("connectors._transport_secret", return_value="token"),
            patch("connectors.poll") as mail,
            patch("connectors._http", side_effect=api),
        ):
            self.engine.sync(c)
        mail.assert_not_called()
        (event,) = self.db.rows("events")
        self.assertEqual(event["raw"]["context"]["google_service"], "Drive")
        self.assertIn("Calendar offline", self.db.get("connections", c["id"])["status"])
        self.assertEqual(len(self.db.rows("google_cursors")), 1)
