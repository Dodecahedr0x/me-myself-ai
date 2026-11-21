"""Fixture transports only. Tests never read the login Keychain or open sockets."""

import ctypes
import datetime as dt
import io
import smtplib
import ssl
import unittest
from email.message import EmailMessage
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import connectors as c


MAIL = dict(address="me@example.test", imap_host="imap.example.test", smtp_host="smtp.example.test")
TOKEN = "fixture-token"


def conn(kind, config):
    return dict(id="test-connection", kind=kind, config=config, label="Fixture", project="Test")


class Providers(unittest.TestCase):
    def setUp(self):
        secret = patch.object(c, "_secret", return_value=TOKEN)
        secret.start()
        self.addCleanup(secret.stop)
        self.guard = patch.object(c, "build_opener", side_effect=AssertionError("Unexpected network"))
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def test_validates_config_and_discards_undeclared_values(self):
        config = c.validate("email", dict(MAIL, secret=TOKEN))
        self.assertEqual((config["imap_port"], config["smtp_port"]), ("993", "465"))
        self.assertNotIn("secret", config)
        for kind, options in [
            ("github", dict(repository="../evil")),
            ("github", dict(repository="a/b?q=x")),
            ("email", dict(MAIL, smtp_port=25)),
            ("slack", dict(channel="channel\nother")),
        ]:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                c.validate(kind, options)

    def test_imap_timezone_peek_targets_and_verified_smtp(self):
        msg = EmailMessage()
        msg["From"], msg["Reply-To"], msg["To"] = (
            "sender@example.test",
            "other@example.test",
            MAIL["address"],
        )
        msg["Subject"], msg["Message-ID"] = "Report", "<report@example.test>"
        msg.set_content("Please send the report.")
        client = Mock()
        client.login.return_value = client.select.return_value = ("OK", [])
        client.response.return_value = ("UIDVALIDITY", [b"33"])
        client.uid.side_effect = [
            ("OK", [b"2 3"]),
            ("OK", [b'1 (INTERNALDATE "01-Dec-2025 00:30:00 +0200")']),
            ("OK", [(b"1 (BODY[]", msg.as_bytes())]),
            ("OK", [b'2 (INTERNALDATE "01-Dec-2025 00:30:01 +0200")']),
        ]
        end = dt.datetime(2025, 11, 30, 22, 30, tzinfo=dt.timezone.utc).timestamp()
        connection = conn("email", dict(MAIL, smtp_port="587"))
        with patch.object(c.imaplib, "IMAP4_SSL") as imap:
            imap.return_value.__enter__.return_value = client
            rows = c.poll(connection, end - 60, end)
        self.assertEqual(imap.call_args.kwargs["ssl_context"].verify_mode, ssl.CERT_REQUIRED)
        client.select.assert_called_once_with('"INBOX"', readonly=True)
        self.assertEqual(sum("BODY.PEEK[]" in str(call) for call in client.uid.call_args_list), 1)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["key"], "INBOX:33:2")
        self.assertEqual(row["timestamp"], end)
        self.assertEqual(row["destination"], "other@example.test")
        with patch.object(c.smtplib, "SMTP", autospec=True) as smtp:
            session = smtp.return_value
            session.send_message.return_value = {}
            self.assertTrue(c.send(connection, row, "Attached tomorrow.").startswith("SMTP accepted <"))
            calls = [call[0] for call in session.method_calls]
            self.assertLess(calls.index("starttls"), calls.index("login"))
            tls = session.starttls.call_args.kwargs["context"]
            self.assertEqual(tls.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(tls.check_hostname)
            args, kwargs = session.send_message.call_args
            self.assertEqual(kwargs["to_addrs"], [row["destination"]])
            self.assertEqual(args[0]["In-Reply-To"], "<report@example.test>")
            self.assertEqual(args[0].get_content(), "Attached tomorrow.\n")

    def test_smtp_dispatch_certainty(self):
        event = {
            "account": MAIL["address"],
            "destination": "someone@example.test",
            "context": {"subject": "Hello", "message_id": "", "recipient": "someone@example.test"},
        }
        with patch.object(c.smtplib, "SMTP_SSL", autospec=True) as smtp:
            session = smtp.return_value
            session.send_message.side_effect = smtplib.SMTPDataError(554, b"message refused")
            with self.assertRaises(c.Rejected):
                c.send(conn("email", MAIL), event, "Approved")
            session.send_message.side_effect = TimeoutError()
            with self.assertRaises(TimeoutError):
                c.send(conn("email", MAIL), event, "Approved")
            self.assertEqual(session.send_message.call_count, 2)

    def test_slack_pagination_old_threads_and_confirmed_reply(self):
        identity = {"ok": True, "team_id": "T12", "user_id": "U7", "url": "https://team.slack.com/"}
        parent = dict(ts="1.000100", text="Old thread", reply_count=1, latest_reply="9.000100")
        reply = dict(ts="9.000100", thread_ts=parent["ts"], user="U7", text="My commitment.")
        page = {
            "messages": [dict(ts="8.000100", text="New", user="U2")],
            "response_metadata": {"next_cursor": "page-two"},
        }
        connection = conn("slack", dict(channel="C123"))
        connection["known_threads"] = [parent["ts"]]
        with patch.object(
            c, "_slack", side_effect=[identity, page, {"messages": []}, {"messages": [parent, reply]}]
        ) as api:
            rows = c.poll(connection, 8, 10)
            self.assertEqual(api.call_args_list[2].args[2]["cursor"], "page-two")
            self.assertTrue(all(call.args[2]["oldest"] == "8.000000" for call in api.call_args_list[1:]))
            self.assertTrue(all(call.kwargs.get("body") is None for call in api.call_args_list))
        self.assertEqual([r["body"] for r in rows], ["New", "My commitment."])
        self.assertTrue(rows[1]["authored"])
        self.assertEqual(rows[1]["account"], "T12/U7")
        with patch.object(c, "_slack", side_effect=[identity, {"channel": "C123", "ts": "20.123"}]) as api:
            self.assertIn("20.123", c.send(connection, rows[1], "Approved reply"))
            self.assertEqual(api.call_args.kwargs["body"]["thread_ts"], parent["ts"])
        for auth, row in [
            (dict(identity, user_id="U99"), rows[1]),
            (identity, dict(rows[1], destination="elsewhere")),
        ]:
            with patch.object(c, "_slack", return_value=auth) as api, self.assertRaises(c.Rejected):
                try:
                    c.send(connection, row, "Reviewed")
                finally:
                    self.assertEqual(api.call_count, 1)

    def test_incomplete_slack_page_fails_entire_poll(self):
        responses = [{"team_id": "T", "user_id": "U"}, {"messages": [], "has_more": True}]
        with (
            patch.object(c, "_slack", side_effect=responses),
            self.assertRaisesRegex(RuntimeError, "incomplete"),
        ):
            c.poll(conn("slack", dict(channel="C123")), 1, 2)

    def test_github_pull_review_assignment_comment_and_page(self):
        base = dict(
            title="Update API",
            body="Please review.",
            user={"login": "owner"},
            state="open",
            updated_at=c._iso(9),
            html_url="https://github.com/a/b/pull/1",
            assignees=[{"login": "elsewhere"}],
            number=1,
            pull_request={},
        )
        comment = dict(
            id=331,
            issue_url="https://api.github.com/repos/a/b/issues/1",
            user={"login": "me"},
            html_url=base["html_url"] + "#issuecomment-331",
            body="I will review it.",
            updated_at=c._iso(10),
        )
        connection = conn("github", dict(repository="a/b"))
        with patch.object(
            c,
            "_gh",
            side_effect=[
                ({"login": "me"}, {}),
                ([base], {"Link": '<page2>; rel="next"'}),
                ({"requested_reviewers": [{"login": "me"}]}, {}),
                ([], {}),
                ([comment], {}),
            ],
        ) as api:
            rows = c.poll(connection, 8, 10)
            self.assertEqual(api.call_args_list[3].args[2]["page"], 2)
            self.assertTrue(all("body" not in call.kwargs for call in api.call_args_list))
        task, post = rows
        self.assertTrue(task["context"]["review_requested"])
        self.assertFalse(task["owned"])
        self.assertTrue(post["authored"])
        with patch.object(
            c, "_gh", side_effect=[({"login": "me"}, {}), ({"html_url": "receipt"}, {})]
        ) as api:
            self.assertEqual(c.send(connection, task, "Reviewed"), "receipt")
            self.assertEqual(api.call_args.args[0], "/repos/a/b/issues/1/comments")
            self.assertEqual(api.call_args.kwargs, {"body": {"body": "Reviewed"}})
        with (
            patch.object(c, "_gh", return_value=({"login": "changed"}, {})) as api,
            self.assertRaises(c.Rejected),
        ):
            try:
                c.send(connection, task, "Reviewed")
            finally:
                self.assertEqual(api.call_count, 1)

    def test_repo_scoped_request(self):
        connection = conn("github", dict(repository="a/b"))
        with patch.object(c, "_http", return_value=({"id": 1}, {})) as api:
            self.assertEqual(
                c.github_request(connection, "PATCH", "issues/17", {"state": "open"}), {"id": 1}
            )
            self.assertEqual(api.call_args.args[0], "https://api.github.com/repos/a/b/issues/17")
            for path in ("/../issues", "%2e%2e/issues", "https://elsewhere/", "pulls?redirect=x"):
                with self.assertRaises(c.Rejected):
                    c.github_request(connection, "POST", path, {})
            self.assertEqual(api.call_count, 1)
            c.github_request(connection, "GET", "")
            self.assertEqual(api.call_args.args[0], "https://api.github.com/repos/a/b")

    def test_sent_activity_matches_delivery_receipts(self):
        sent = {"authored": True, "context": {"message_id": "<reply@example.test>"}}
        self.assertEqual(c.authored_receipt(sent), "SMTP accepted <reply@example.test>")
        self.assertEqual(c.authored_receipt(dict(sent, authored=False)), "")
        slack = {"authored": True, "key": "T/C1/12.345", "context": {"channel": "C1"}}
        self.assertEqual(c.authored_receipt(slack), "Slack C1 / 12.345")
        comment = {
            "authored": True,
            "key": "comment:a/b:1",
            "url": "https://github.com/a/b/issues/2#issuecomment-1",
        }
        self.assertEqual(c.authored_receipt(comment), comment["url"])

    def test_http_write_never_retried(self):
        provider = Mock()
        provider.open.side_effect = HTTPError(
            "https://example.test", 429, "slow down", {"Retry-After": "10"}, io.BytesIO()
        )
        with patch.object(c, "build_opener", return_value=provider), self.assertRaises(c.Rejected):
            c._http("https://example.test", TOKEN, body={"body": "Approved"})
        self.assertEqual(provider.open.call_count, 1)

    def test_keychain_uses_generic_password_and_native_memory_cleanup(self):
        security, core = Mock(), Mock()
        security.SecKeychainFindGenericPassword.return_value = -25300
        security.SecKeychainAddGenericPassword.return_value = 0
        with patch.object(ctypes, "CDLL", side_effect=[security, core]):
            c.store_secret("fixture", TOKEN)
        args = security.SecKeychainAddGenericPassword.call_args.args
        self.assertEqual(args[2], b"memyselfai.connectors")
        self.assertEqual(args[4:7], (b"fixture", len(TOKEN), TOKEN.encode()))
        buf = ctypes.create_string_buffer(TOKEN.encode())

        def lookup(*args):
            args[-3]._obj.value = len(TOKEN)
            args[-2]._obj.value = ctypes.addressof(buf)
            args[-1]._obj.value = 1234
            return 0

        security.SecKeychainFindGenericPassword.side_effect = lookup
        with patch.object(ctypes, "CDLL", side_effect=[security, core]):
            self.assertEqual(c._Keychain().access("read", "fixture"), TOKEN)
        security.SecKeychainItemFreeContent.assert_called_once()
        core.CFRelease.assert_called_once()
