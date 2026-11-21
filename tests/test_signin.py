"""Connection actions, credential renewal and mail transport with simulated accounts."""

import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import connectors
from engine import Engine


class SignInTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.engine = Engine(folder.name)
        self.addCleanup(self.engine.close)
        browser = patch("signin.webbrowser.open")
        browser.start()
        self.addCleanup(browser.stop)
        self.keys = {}
        for name, operation in [
            ("store_secret", self.keys.__setitem__),
            ("_secret", self.keys.__getitem__),
            ("delete_secret", self.keys.__delitem__),
        ]:
            fixture = patch.object(connectors, name, side_effect=operation)
            fixture.start()
            self.addCleanup(fixture.stop)
        fixture = patch("signin.CLIENTS_FILE", Path(folder.name) / "oauth-clients.json")
        self.clients = fixture.start()
        self.addCleanup(fixture.stop)
        self.email = "me@example.test"
        self.credential = dict(
            provider="google",
            client_id="test.apps.googleusercontent.com",
            access_token="fixture-access",
            refresh_token="fixture-refresh",
            expires_at=1,
        )
        self.google = {
            "account": self.email,
            "identity": self.email,
            "options": [{"value": self.email, "label": self.email}],
            "credential": self.credential,
        }

    def action(self, op, **data):
        return self.engine.action(dict(op=op, **data))

    def ready(self, kind, grant, **data):
        handle = Mock(url="https://provider.test/consent")
        handle.wait.return_value = grant
        fixture = (
            patch("signin.github_signin.authorize", return_value=grant)
            if kind == "github"
            else patch("signin.account_oauth.begin", return_value=handle)
        )
        with fixture:
            flow = self.action("auth_start", kind=kind, **data)["flow"]
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = self.action("auth_status", flow=flow)
                if status["phase"] != "waiting":
                    self.assertEqual(
                        status, {"phase": "ready", "account": grant["account"], "options": grant["options"]}
                    )
                    return flow
                time.sleep(0.005)
            self.fail("The simulated provider did not finish sign-in")

    def google_connection(self):
        self.clients.write_text(json.dumps({"email": {"client_id": self.credential["client_id"]}}))
        flow = self.ready("email", self.google)
        self.action("auth_finish", flow=flow, value=self.email)
        return self.engine.db.rows("connections")[0]

    def test_github_account_setup_needs_no_repository_or_local_token(self):
        grant = {"account": "me", "options": []}
        flow = self.ready("github", grant)
        self.action("auth_finish", flow=flow)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.action("auth_finish", flow=flow)
        (saved,) = self.engine.state()["connections"]
        self.assertEqual(saved["auth_method"], "gh_account")
        self.assertNotIn("repository", saved["config"])
        flow = self.ready("github", dict(grant, account="another"), id=saved["id"])
        with self.assertRaisesRegex(ValueError, "original GitHub account"):
            self.action("auth_finish", flow=flow)
        self.action("auth_cancel", flow=flow)
        flow = self.ready("github", grant)
        self.action("auth_finish", flow=flow)
        self.assertEqual(len(self.engine.db.rows("connections")), 1)
        self.assertEqual(self.keys, {})
        self.action("disconnect", id=saved["id"])

    @patch("signin.webbrowser.open")
    def test_slack_channels_share_one_grant(self, browser):
        self.assertFalse(self.action("auth_info", kind="slack")["configured"])
        self.clients.write_text(json.dumps({"slack": {"client_id": "123.456"}}))
        grant = dict(
            account="Fixture",
            identity="T1/U1",
            options=[dict(value=c, label="#" + c) for c in ["C1", "C2", "C3"]],
            credential=dict(self.credential, provider="slack", client_id="123.456"),
        )
        flow = self.ready("slack", grant)
        browser.assert_called_once()
        for invalid in ([], ["C1", "unverified"]):
            with self.assertRaises(ValueError):
                self.action("auth_finish", flow=flow, values=invalid)
            self.assertEqual(self.engine.db.rows("connections"), [])
            self.assertEqual(self.keys, {})
        with patch("connectors.store_secret", side_effect=ValueError("Keychain unavailable")):
            with self.assertRaisesRegex(ValueError, "Keychain unavailable"):
                self.action("auth_finish", flow=flow, values=["C1", "C2"])
        self.assertEqual(self.engine.db.rows("connections"), [])
        self.engine.connect(
            dict(kind="slack", config=dict(channel="C1"), label="#legacy", secret="old-credential"),
            verified=dict(auth_method="slack", identity="T1/U1"),
        )
        legacy_id = self.engine.db.rows("connections")[0]["id"]
        self.action("auth_finish", flow=flow, values=["C1", "C2", "C1"])
        first, second = self.engine.db.rows("connections")
        self.assertEqual([c["config"]["channel"] for c in (first, second)], ["C1", "C2"])
        self.assertEqual(len(self.keys), 1)
        self.assertEqual(first["id"], legacy_id)
        self.assertNotIn(legacy_id, self.keys)
        self.assertEqual(first["credential_key"], second["credential_key"])
        fresh = dict(grant["credential"], access_token="renewed", expires_at=time.time() + 3600)
        with patch("connectors.account_oauth.refresh", return_value=fresh) as refresh:
            with ThreadPoolExecutor(2) as pool:
                tokens = list(
                    pool.map(lambda c: connectors._transport_secret(c, c["config"]), [first, second])
                )
            self.assertEqual(tokens, ["renewed", "renewed"])
            refresh.assert_called_once()
        self.action("auth_finish", flow=self.ready("slack", grant), values=["C2", "C3"])
        self.assertEqual(
            [c["config"]["channel"] for c in self.engine.db.rows("connections")], ["C1", "C2", "C3"]
        )
        flow = self.ready("slack", grant, id=second["id"])
        with self.assertRaisesRegex(ValueError, "original channel"):
            self.action("auth_finish", flow=flow, values=["C3"])
        self.action("auth_finish", flow=flow, value="C2")
        for c in self.engine.db.rows("connections"):
            self.assertEqual(len(self.keys), 1)
            self.action("disconnect", id=c["id"])
        self.assertEqual(self.keys, {})

    def wait_for_credential(self, kind, **fields):
        flow = self.action("auth_start", kind=kind, **fields)["flow"]
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = self.action("auth_status", flow=flow)
            if status["phase"] != "waiting":
                return flow, status
            time.sleep(0.005)
        self.fail("Credential verification did not finish")

    def test_user_token_without_registration(self):
        token = "xoxp-fixture-token"
        identity = dict(ok=True, team_id="T1", user_id="U1", team="Fixture")
        channels = dict(ok=True, channels=[dict(id=c, name=c.lower()) for c in ("C1", "C2")])
        with patch("account_oauth._request", side_effect=[identity, channels]) as request:
            flow, state = self.wait_for_credential("slack", method="slack_token", secret=token)
        self.assertEqual(state["phase"], "ready")
        self.assertNotIn(token, str(state))
        self.assertEqual(request.call_args.kwargs["token"], token)
        self.assertFalse(self.keys)
        self.action("auth_finish", flow=flow, values=["C1", "C2"])
        conns = self.engine.db.rows("connections")
        self.assertEqual(len(conns), 2)
        self.assertNotIn(token, str(self.engine.state()))
        self.assertEqual(connectors._transport_secret(conns[0], conns[0]["config"]), token)
        with patch("account_oauth._request", return_value=dict(identity, bot_id="B1")):
            _, status = self.wait_for_credential("slack", method="slack_token", secret=token)
        self.assertEqual(status["phase"], "error")

    def test_workspace_pair_rotates_and_shares_replacements_across_channels(self):
        original = "xoxe.xoxp-original"
        refresh = "xoxe-1-original"
        secret = "fixture-client-secret"
        who = dict(ok=True, team_id="T1", user_id="U1")
        channels = dict(ok=True, channels=[dict(id=x, name=x) for x in ["C1", "C2"]])
        form = dict(
            method="slack_token",
            secret=original,
            refresh_token=refresh,
            client_id="123.456",
            client_secret=secret,
        )
        with patch("account_oauth._request", side_effect=[who, channels]) as api:
            flow, state = self.wait_for_credential("slack", **form)
            self.assertEqual(state["phase"], "ready")
            self.action("auth_cancel", flow=flow)
            self.assertEqual(api.call_count, 2)
            self.assertFalse(self.keys)
        with patch("account_oauth._request", side_effect=[who, channels]):
            flow, _ = self.wait_for_credential("slack", **form)

        def rotated(n):
            return dict(
                ok=True,
                token_type="user",
                access_token="xoxe.xoxp-" + str(n),
                refresh_token="xoxe-1-" + str(n),
                expires_in=43200,
            )

        with patch("account_oauth._request", side_effect=[rotated(1), who]) as api:
            self.action("auth_finish", flow=flow)
            self.assertEqual(
                api.call_args_list[0].kwargs["form"],
                dict(
                    client_id="123.456",
                    client_secret=secret,
                    grant_type="refresh_token",
                    refresh_token=refresh,
                ),
            )
        conns = [c for c in self.engine.db.rows("connections") if c.get("slack_parent")]
        self.assertEqual(len(conns), 2)
        key = conns[0]["credential_key"]
        self.assertEqual(len(self.keys), 1)
        for n in [2, 3]:
            old = json.loads(self.keys[key][len(connectors.OAUTH_PREFIX) :])
            self.keys[key] = connectors.OAUTH_PREFIX + json.dumps(dict(old, expires_at=1))
            with (
                patch("account_oauth._request", return_value=rotated(n)) as api,
                ThreadPoolExecutor(2) as pool,
            ):
                self.assertEqual(
                    list(pool.map(lambda c: connectors._transport_secret(c, {}), conns)),
                    ["xoxe.xoxp-" + str(n)] * 2,
                )
                api.assert_called_once()
                self.assertEqual(
                    api.call_args.kwargs["form"]["refresh_token"], rotated(n - 1)["refresh_token"]
                )
        for value in [secret, original, refresh, rotated(3)["refresh_token"]]:
            self.assertNotIn(value, str(self.engine.state()))
            self.assertNotIn(value, "\n".join(self.engine.db.conn.iterdump()))

    def test_refresh_only_uses_public_client_and_rejects_bot_grant(self):
        self.clients.write_text(json.dumps({"slack": {"client_id": "123.456"}}))
        who = dict(ok=True, team_id="T1", user_id="U1")
        fresh = dict(
            ok=True,
            access_token="xoxe.xoxp-new",
            refresh_token="xoxe-1-new",
            token_type="user",
            expires_in=43200,
        )
        with patch("account_oauth._request", side_effect=[fresh, who, dict(ok=True, channels=[])]) as api:
            flow, state = self.wait_for_credential(
                "slack", method="slack_token", refresh_token="xoxe-1-original"
            )
            self.assertEqual(state["phase"], "ready")
            self.action("auth_finish", flow=flow)
            self.assertNotIn("client_secret", api.call_args_list[0].kwargs["form"])
            self.assertEqual(api.call_args_list[1].kwargs["token"], fresh["access_token"])
        saved = dict(self.keys)
        with patch("account_oauth._request", return_value=dict(fresh, token_type="bot")):
            _, state = self.wait_for_credential("slack", method="slack_token", refresh_token="xoxe-1-bot")
        self.assertEqual(state["phase"], "error")
        self.assertEqual(saved, self.keys)

    def test_pair_cannot_switch_to_different_workspace(self):
        who = dict(ok=True, team_id="T1", user_id="U1")
        with patch("account_oauth._request", side_effect=[who, dict(ok=True, channels=[])]):
            flow, _ = self.wait_for_credential(
                "slack",
                method="slack_token",
                secret="xoxp-user",
                refresh_token="xoxe-1-other",
                client_id="123.456",
            )
        fresh = dict(
            ok=True, access_token="new", refresh_token="new-refresh", token_type="user", expires_in=43200
        )
        with (
            patch("account_oauth._request", side_effect=[fresh, dict(who, team_id="T2")]),
            self.assertRaisesRegex(ValueError, "different accounts"),
        ):
            self.action("auth_finish", flow=flow)
        self.assertFalse(self.engine.db.rows("connections"))
        self.assertFalse(self.keys)

    def test_google_app_password_accounts_and_cancel(self):
        password = "abcd efgh ijkl mnop"
        with patch("connectors.imaplib.IMAP4_SSL") as imap:
            client = imap.return_value.__enter__.return_value
            client.login.return_value = ("OK", [])
            for address in ("work@example.test", "personal@example.test"):
                flow, status = self.wait_for_credential(
                    "email", method="gmail_password", address=address, secret=password
                )
                self.assertEqual(status["phase"], "ready")
                client.login.assert_called_with(address, password.replace(" ", ""))
                self.action("auth_finish", flow=flow, value=address)
            flow, _ = self.wait_for_credential(
                "email", method="gmail_password", address="cancel@example.test", secret=password
            )
            self.action("auth_cancel", flow=flow)
            client.login.return_value = ("NO", [])
            _, status = self.wait_for_credential(
                "email", method="gmail_password", address="bad@example.test", secret=password
            )
            self.assertEqual(status["phase"], "error")
        self.assertEqual(len(self.keys), 2)
        first, second = self.engine.db.rows("connections")
        config = first["config"].copy()
        self.assertEqual(connectors._transport_secret(first, config), password.replace(" ", ""))
        self.assertTrue(config["gmail_password"])
        self.assertFalse(config.get("xoauth2"))
        self.assertNotEqual(first["id"], second["id"])
        with self.assertRaisesRegex(ValueError, "Google servers"):
            connectors._transport_secret(first, dict(config, smtp_host="example.test"))
        self.assertNotIn(password.replace(" ", ""), str(self.engine.state()))

    def test_registration_reuse_and_secret_visibility(self):
        self.assertFalse(self.action("auth_info", kind="email")["configured"])
        self.action(
            "auth_configure",
            kind="email",
            client_id=self.credential["client_id"],
            client_secret="fixture-client-secret",
        )
        self.assertTrue(self.action("auth_info", kind="email")["configured"])
        for index in range(2):
            self.engine.add_project(str(index), "")
            flow = self.ready("email", self.google, project=str(index))
            self.action("auth_cancel", flow=flow)
        self.assertEqual(len(self.keys), 1)
        self.assertEqual(self.engine.db.rows("connections"), [])
        dump = "\n".join(self.engine.db.conn.iterdump())
        for secret in ("fixture-access", "fixture-client-secret", "fixture-refresh"):
            self.assertNotIn(secret, dump)

    def test_concurrent_refresh_and_endpoint_binding(self):
        conn = self.google_connection()
        for key, value in [
            ("smtp_host", "example.test"),
            ("imap_port", "7993"),
            ("address", "other@example.test"),
        ]:
            with self.assertRaisesRegex(ValueError, "verified Gmail"):
                connectors._transport_secret(conn, dict(conn["config"], **{key: value}))
        fresh = dict(self.credential, access_token="renewed-access", expires_at=time.time() + 3600)
        with patch("account_oauth.refresh", return_value=fresh) as refresh, ThreadPoolExecutor(4) as pool:

            def read(_):
                return connectors._transport_secret(conn, dict(conn["config"]))

            self.assertEqual(list(pool.map(read, range(4))), ["renewed-access"] * 4)
            refresh.assert_called_once()
        self.assertNotIn("renewed-access", "\n".join(self.engine.db.conn.iterdump()))
        self.assertIn("renewed-access", self.keys[conn["id"]])

    def test_gmail_sasl_and_discovered_sent_mailbox(self):
        self.credential["expires_at"] = time.time() + 600
        conn = self.google_connection()
        client = Mock()
        client.authenticate.return_value = client.select.return_value = ("OK", [])
        client.response.return_value = ("UIDVALIDITY", [b"1"])
        client.list.return_value = ("OK", [rb'(\HasNoChildren \Sent) "/" "[Gmail]/Sent Mail"'])
        client.uid.side_effect = [
            ("OK", [b""]),
            ("OK", [b"7"]),
            ("OK", [b'1 (INTERNALDATE "01-Dec-2025 00:30:00 +0000")']),
            (
                "OK",
                [(b"", b"From: me@example.test\r\nTo: you@example.test\r\nSubject: Report\r\n\r\nSent.")],
            ),
        ]
        with patch.object(connectors.imaplib, "IMAP4_SSL") as imap:
            imap.return_value.__enter__.return_value = client
            (row,) = connectors.poll(conn, 1764547200, 1764550800)
        self.assertTrue(row["authored"])
        self.assertEqual(row["destination"], "you@example.test")
        mech, response = client.authenticate.call_args.args
        self.assertEqual(mech, "XOAUTH2")
        self.assertEqual(response(b""), b"user=me@example.test\x01auth=Bearer fixture-access\x01\x01")
        self.assertEqual(response(b"error"), b"")
        with patch.object(connectors.smtplib, "SMTP_SSL", autospec=True) as smtp:
            session = smtp.return_value
            session.send_message.return_value = {}
            connectors.send(conn, row, "A reviewed reply")
            self.assertEqual(
                [c[0] for c in session.method_calls][:3], ["ehlo_or_helo_if_needed", "auth", "send_message"]
            )
            self.assertEqual(session.auth.call_args.args[1]().encode(), response(b""))
