"""Google onboarding and approved operations on disposable accounts."""

import base64
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import connectors
import google_workspace
import workspace_bridge as bridge
from engine import Engine
from store import uid


class GoogleConnectorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.keys = {}
        for name, operation in [
            ("store_secret", self.keys.__setitem__),
            ("delete_secret", self.keys.pop),
            ("_secret", self.keys.__getitem__),
        ]:
            mock = patch.object(connectors, name, side_effect=operation)
            mock.start()
            self.addCleanup(mock.stop)
        self.engine = Engine(self.root)
        self.addCleanup(self.engine.close)

    def fixture_flow(self, email, **options):
        session = patch.object(bridge, "session")
        with session as simulated, patch.object(self.engine.signins, "_registration", return_value=None):
            client = simulated.return_value.__enter__.return_value
            client.cancel = None

            def respond(tool, args, *unused):
                if tool == "auth_accountEmail":
                    return {"email": email}
                if tool == "drive_search":
                    raise ValueError("Denied")
                return [] if tool == "calendar_listEvents" else {"messages": []}

            client.call.side_effect = respond
            flow = self.engine.action(dict(op="auth_start", kind="email", **options))["flow"]
            for _ in range(200):
                result = self.engine.action(dict(op="auth_status", flow=flow))
                if result["phase"] != "waiting":
                    break
                time.sleep(0.01)
            self.assertEqual(result["phase"], "ready", result)
            self.assertEqual(result["services"], ["Gmail", "Calendar"])
            return flow

    def connect(self, email):
        flow = self.fixture_flow(email)
        self.engine.action(dict(op="auth_finish", flow=flow, value=email))
        return next(c for c in self.engine.db.rows("connections") if c["identity"] == email)

    def test_multiple_accounts_cancel_reconnect_and_disconnect(self):
        first = self.connect("first@example.test")
        self.connect("second@example.test")
        bad = self.fixture_flow("other@example.test", id=first["id"])
        with self.assertRaisesRegex(ValueError, "original account"):
            self.engine.action(dict(op="auth_finish", flow=bad, value="other@example.test"))
        self.engine.action(dict(op="auth_cancel", flow=bad))
        other = self.fixture_flow("other@example.test")
        self.engine.action(dict(op="auth_cancel", flow=other))
        new = self.connect("first@example.test")
        self.assertEqual(first["id"], new["id"])
        self.assertNotEqual(first["workspace_profile"], new["workspace_profile"])
        self.assertFalse(bridge.profile_path(self.root, first["workspace_profile"]).exists())
        repeated = self.fixture_flow("FIRST@example.test")
        self.engine.action(dict(op="auth_finish", flow=repeated, value="FIRST@example.test"))
        self.assertEqual(len(self.engine.db.rows("connections")), 2)
        self.assertEqual(self.engine.db.get("connections", first["id"])["identity"], first["identity"])
        self.assertEqual(len(self.keys), 2)
        for c in self.engine.db.rows("connections"):
            self.assertNotIn(
                self.keys["workspace-key:" + c["workspace_profile"]], json.dumps(self.engine.state())
            )
            self.engine.action(dict(op="disconnect", id=c["id"]))
        self.assertEqual(list((self.root / "google-accounts").iterdir()), [])
        self.assertEqual(self.keys, {})

    def test_reconnect_upgrades_one_account_and_polls_tasks_and_contacts(self):
        email = "me@example.test"
        old = self.connect(email)
        profile = bridge.profile_path(self.root, old["workspace_profile"])
        deadline = self.engine.now() + 900
        self.engine.followups("source", "Global", [dict(title="Send report", due=deadline)], "team")
        task = next(t for t in self.engine.db.rows("tasks") if t["kind"] == "calendar")
        self.engine.process(task["id"])
        preview = self.engine.db.get("tasks", task["id"])
        self.assertEqual(preview["status"], "review")
        publisher = self.root / "oauth-clients.json"
        publisher.write_text(json.dumps({"email": {"client_id": "fixture.apps.googleusercontent.com"}}))
        services = ["Calendar", "Tasks", "Contacts"]
        handle = Mock(url="https://accounts.google.com/fixture")
        handle.wait.return_value = dict(
            account=email,
            identity=email,
            options=[dict(value=email, label=email)],
            services=services,
            credential=dict(provider="google", access_token="fixture", refresh_token="refresh"),
        )
        with (
            patch("signin.CLIENTS_FILE", publisher),
            patch("signin.webbrowser.open"),
            patch("signin.account_oauth.begin", return_value=handle) as begin,
        ):
            flow = self.engine.action(dict(op="auth_start", kind="email", id=old["id"]))["flow"]
            for _ in range(200):
                status = self.engine.action(dict(op="auth_status", flow=flow))
                if status["phase"] != "waiting":
                    break
                time.sleep(0.01)
            self.assertEqual(status["phase"], "ready", status)
            self.assertTrue(profile.is_dir())
            self.engine.action(dict(op="auth_finish", flow=flow, value=email))
            begin.assert_called_once_with("email", {"client_id": "fixture.apps.googleusercontent.com"})
        (current,) = self.engine.db.rows("connections")
        self.assertEqual(current["id"], old["id"])
        self.assertEqual(current["google_services"], services)
        self.assertFalse(profile.exists())
        self.assertNotIn("workspace_profile", current)
        self.assertNotIn("workspace-key:" + old["workspace_profile"], self.keys)
        with self.assertRaisesRegex(ValueError, "preview changed"):
            self.engine.deliver(task["id"], preview["version"])
        self.engine.process(task["id"])
        self.assertIn("extendedProperties", self.engine.db.get("tasks", task["id"])["calendar_payload"])
        now = self.engine.now()
        stamp = google_workspace.utc(now - 1)

        def api(url, token, params):
            self.assertEqual(token, "fixture")
            if url.endswith("/users/@me/lists"):
                return {"items": [{"id": "list"}]}, {}
            if url.endswith("/lists/list/tasks"):
                return {"items": [{"id": "task", "title": "Send figures", "updated": stamp}]}, {}
            if url.endswith("/people/me/connections"):
                return {
                    "connections": [
                        {
                            "resourceName": "people/c1",
                            "names": [{"displayName": "Alex"}],
                            "metadata": {"sources": [{"updateTime": stamp}]},
                        }
                    ]
                }, {}
            self.assertTrue(url.endswith("/calendars/primary/events"))
            return {"items": []}, {}

        with patch("connectors._http", side_effect=api):
            self.engine.sync_google(current, (now - 10800, now))
        events = self.engine.db.rows("events")
        self.assertEqual({e["raw"]["title"] for e in events}, {"Send figures", "Alex"})
        self.assertEqual({e["connection"] for e in events}, {old["id"]})

    def test_calendar_write_matches_dry_mode_preview_and_recognizes_previous_receipt(self):
        account = self.connect("first@example.test")
        deadline = self.engine.now() + 900
        self.engine.followups("source", "Global", [dict(title="Send update", due=deadline)], "team")
        task = next(t for t in self.engine.db.rows("tasks") if t["kind"] == "calendar")
        with patch.object(bridge, "session") as session:
            client = session.return_value.__enter__.return_value
            self.engine.process(task["id"])
            task = self.engine.db.get("tasks", task["id"])
            self.assertEqual(task["status"], "review")
            with self.assertRaisesRegex(ValueError, "review"):
                self.engine.deliver(task["id"], task["version"], automatic=True)
            session.assert_not_called()
            body = json.loads(task["draft"])
            me = {"email": account["identity"]}
            client.call.side_effect = [me, [], {"id": "123"}]
            self.engine.action(dict(op="approve", id=task["id"], version=task["version"]))
            self.assertEqual(client.call.call_args.args, ("calendar_createEvent", body))
            self.assertEqual(body["sendUpdates"], "none")
            self.assertEqual(self.engine.db.get("tasks", task["id"])["status"], "done")
            client.call.reset_mock()
            client.call.side_effect = [me, [dict(body, id="123")]]
            self.assertEqual(bridge.create_event(self.root, account, task), "Calendar event 123")
            self.assertEqual(client.call.call_count, 2)

    def test_poll_reads_pages_and_binds_gmail_thread_and_recipient(self):
        email = "me@example.test"
        message = b"From: author@example.test\r\nReply-To: reply@example.test\r\nSubject: Deadline\r\n\r\nSend the report"
        msg = {
            "id": "m",
            "threadId": "thread",
            "labelIds": ["INBOX"],
            "internalDate": "100000",
            "raw": base64.urlsafe_b64encode(message).decode(),
        }
        client = Mock()
        client.call.side_effect = [
            {"messages": [], "nextPageToken": "page2"},
            {"messages": [{"id": "m"}]},
            msg,
        ]
        (observed,) = list(bridge.messages(client, email, 99, 101))
        self.assertEqual(observed["destination"], "reply@example.test")
        self.assertEqual(observed["body"], "Send the report")
        self.assertEqual(observed["context"]["thread"], "thread")
        self.assertFalse(observed["authored"])
        self.assertEqual(client.call.call_args_list[1].args[1]["pageToken"], "page2")
        self.assertEqual(
            client.call.call_args_list[-1].args, ("gmail_get", dict(messageId="m", format="raw"))
        )

    def test_restart_cleans_abandoned_and_preserves_connected_profile(self):
        first = self.connect("first@example.test")
        abandoned = uid()
        bridge.make_profile(self.root, abandoned)
        active = bridge.profile_path(self.root, first["workspace_profile"])
        master = active / ".gemini-cli-workspace-master-key"
        master.touch()
        from signin import SignIns

        SignIns(self.engine)
        self.assertFalse(master.exists())
        self.assertTrue(active.exists())
        self.assertFalse(bridge.profile_path(self.root, abandoned).exists())
        self.assertEqual(list(self.keys), ["workspace-key:" + first["workspace_profile"]])

    def test_gmail_reply_stays_in_reviewed_thread(self):
        account = self.connect("first@example.test")
        event = dict(
            account=account["identity"],
            destination="a@example.test",
            context=dict(recipient="a@example.test", subject="Status", gmail_id="m", thread="t"),
        )
        with patch.object(bridge, "session") as session:
            client = session.return_value.__enter__.return_value
            with self.assertRaises(connectors.Rejected):
                bridge.reply(self.root, account, dict(event, destination="b@example.test"), "reply")
            session.assert_not_called()
            client.call.side_effect = [
                {"email": account["identity"]},
                {"id": "d", "message": {"threadId": "t"}},
                {"id": "s", "threadId": "t"},
            ]
            self.assertEqual(bridge.reply(self.root, account, event, "reply"), "Gmail sent s")
            self.assertEqual(
                client.call.call_args_list[-2].args,
                (
                    "gmail_createDraft",
                    dict(to="a@example.test", subject="Re: Status", body="reply", threadId="t"),
                ),
            )

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("node"), "macOS connector runtime")
    def test_published_connector_opens_google_consent_and_cancels(self):
        # Substitute only the OS browser launcher. No account or live Google service is used.
        launcher = self.root / "open"
        marker = self.root / "browser-handoff"
        launcher.write_text(
            "#!"
            + sys.executable
            + "\n"
            + """
import sys
from urllib.parse import urlparse, parse_qs
from pathlib import Path
url=urlparse(sys.argv[1])
args=parse_qs(url.query)
if url.scheme=="https" and url.hostname=="accounts.google.com" and set(args["prompt"][0].split()) == {"select_account","consent"} and args.get("state") and args.get("response_type")==["code"] and args.get("redirect_uri")==["https://google-workspace-extension.geminicli.com"]:
    Path(__file__).with_name("browser-handoff").touch()
"""
        )
        launcher.chmod(0o700)
        ident = uid()
        bridge.make_profile(self.root, ident)
        cancel = threading.Event()
        with patch.dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"]):
            with bridge.session(self.root, ident, cancel) as client:
                names = {tool["name"] for tool in client.request("tools/list", {})["tools"]}
                self.assertTrue(
                    {
                        "auth_accountEmail",
                        "gmail_search",
                        "gmail_createDraft",
                        "gmail_sendDraft",
                        "calendar_createEvent",
                        "drive_search",
                        "docs_getText",
                        "sheets_getText",
                        "slides_getText",
                    }
                    <= names
                )

                def stop():
                    for _ in range(150):
                        if marker.exists():
                            break
                        time.sleep(0.05)
                    cancel.set()

                watcher = threading.Thread(target=stop)
                watcher.start()
                try:
                    with self.assertRaises(ValueError):
                        bridge.address(client, 15)
                finally:
                    watcher.join()
                self.assertTrue(marker.exists(), "The connector did not invoke the Google consent URL.")
        folder = bridge.profile_path(self.root, ident)
        self.assertFalse((folder / ".gemini-cli-workspace-master-key").exists())
        bridge.forget(self.root, ident)
        self.assertFalse(self.keys)
