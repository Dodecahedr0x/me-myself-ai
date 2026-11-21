import base64
import hashlib
import http.client
import json
import socket
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import patch

import account_oauth as oauth


def query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


class BrowserSignInTests(unittest.TestCase):
    def setUp(self):
        self.client = {"client_id": "desktop-client", "client_secret": "fixture-client-secret"}
        self.cancel = threading.Event()

    def begin(self, kind="email"):
        # Bind ephemeral ports even for Slack, so independent test suites can run in parallel.
        with patch.object(oauth, "SLACK_CALLBACK_PORT", 0):
            self.attempt = oauth.begin(kind, self.client)
        self.addCleanup(self.attempt.close)
        self.args = query(self.attempt.url)
        self.callback = urlsplit(self.args["redirect_uri"])
        return self.attempt

    def callback_request(self, fields, *, host=None, path=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.callback.port, timeout=3)
        try:
            conn.request(
                "GET",
                (path or self.callback.path) + "?" + urlencode(fields),
                headers={"Host": host or self.callback.netloc},
            )
            response = conn.getresponse()
            return response.status, response.read().decode()
        finally:
            conn.close()

    def finish(self, *replies):
        with patch.object(oauth, "_request", side_effect=replies) as request, ThreadPoolExecutor() as pool:
            result = pool.submit(self.attempt.wait, self.cancel, 5)
            self.assertEqual(
                self.callback_request({"code": "fixture-code", "state": self.args["state"]})[0], 200
            )
            return result.result(timeout=5), request

    def assert_closed(self):
        with self.assertRaises(OSError):
            with socket.create_connection(("127.0.0.1", self.callback.port), timeout=1):
                self.fail("Callback listener remained open")

    def test_google_sign_in_requires_exact_callback_and_matching_proof(self):
        attempt = self.begin()
        self.assertEqual(self.callback.hostname, "127.0.0.1")
        self.assertEqual(self.callback.path, "/oauth/callback")
        self.assertEqual(self.args["code_challenge_method"], "S256")
        self.assertEqual(self.args["access_type"], "offline")
        self.assertEqual(set(self.args["scope"].split()), {"openid", "email", *oauth.GOOGLE_SCOPES.values()})
        self.assertNotIn(self.client["client_secret"], attempt.url)
        grant = dict(access_token="mail-token", refresh_token="mail-renew", expires_in=3600)
        identity = dict(email="me@example.test", email_verified=True)
        with (
            patch.object(oauth, "_request", side_effect=[grant, identity]) as request,
            ThreadPoolExecutor() as pool,
        ):
            result = pool.submit(attempt.wait, self.cancel, 5)
            valid = {"state": self.args["state"], "code": "private-code"}
            for fields, extras in [
                (dict(valid, state="different"), {}),
                (dict(valid, state="é"), {}),
                (valid, {"host": "elsewhere.test"}),
                (valid, {"path": "/callback"}),
            ]:
                status, response = self.callback_request(fields, **extras)
                self.assertIn(status, (400, 404))
                self.assertNotIn("private-code", response)
            request.assert_not_called()
            status, response = self.callback_request(valid)
            self.assertEqual(status, 200)
            self.assertNotIn("private-code", response)
            signed = result.result(timeout=5)
        self.assert_closed()
        self.assertEqual(signed["account"], identity["email"])
        self.assertEqual(signed["identity"], identity["email"])
        self.assertEqual(signed["options"], [{"value": identity["email"], "label": identity["email"]}])
        self.assertEqual(signed["credential"]["provider"], "google")
        self.assertEqual(signed["credential"]["client_secret"], self.client["client_secret"])
        exchange = request.call_args_list[0]
        self.assertEqual(exchange.args, oauth.TOKEN_ENDPOINTS["google"])
        form = exchange.kwargs["form"]
        proof = (
            base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        self.assertEqual(self.args["code_challenge"], proof)
        self.assertEqual(form["redirect_uri"], self.args["redirect_uri"])
        self.assertEqual(form["code"], "private-code")

    def test_slack_sign_in_paginates_memberships_with_user_credential(self):
        self.begin("slack")
        self.assertEqual(oauth.SLACK_CALLBACK_PORT, 43827)
        self.assertEqual(self.callback.hostname, "localhost")
        self.assertEqual(set(self.args["user_scope"].split(",")), set(oauth.SLACK_SCOPES))
        self.assertNotIn("scope", self.args)
        user = dict(id="U1", scope=self.args["user_scope"], token_type="user", access_token="slack-user")
        install = dict(authed_user=user, access_token="irrelevant-bot")
        who = dict(user_id="U1", team_id="T1", user="zoe", team="Example")
        first = dict(
            channels=[dict(id="C2", name="two"), dict(id="C3", name="old", is_archived=True)],
            response_metadata={"next_cursor": "page 2"},
        )
        second = dict(channels=[dict(id="G1", name="private"), dict(id="C7", name="other", is_member=False)])
        signed, req = self.finish(install, who, first, second)
        self.assertEqual(signed["account"], "zoe (Example)")
        self.assertEqual(signed["identity"], "T1/U1")
        self.assertEqual(
            signed["options"],
            [
                {"value": "C7", "label": "#other"},
                {"value": "G1", "label": "#private"},
                {"value": "C2", "label": "#two"},
            ],
        )
        cred = signed["credential"]
        self.assertEqual((cred["refresh_token"], cred["expires_at"]), ("", 0))
        self.assertNotIn("client_secret", cred)
        self.assertNotIn("client_secret", req.call_args_list[0].kwargs["form"])
        for call in req.call_args_list[1:]:
            self.assertEqual(call.kwargs["token"], "slack-user")
        self.assertEqual(query(req.call_args.args[1])["cursor"], "page 2")

    def test_unverified_email_or_missing_mail_scope_rejected(self):
        grant = dict(access_token="mail", refresh_token="renew", expires_in=300)
        for response, profile, message in [
            (grant, {"email": "foo@example.test", "email_verified": False}, "verify"),
            (dict(grant, scope="openid email"), {}, "Workspace access"),
            (dict(grant, refresh_token=""), {}, "offline access"),
        ]:
            with self.subTest(message=message):
                self.begin()
                with self.assertRaisesRegex(oauth.SignInError, message):
                    self.finish(response, profile)
                self.assert_closed()

    def test_bot_scope_failure_identity_mismatch_or_partial_channels_rejected(self):
        scopes = ",".join(oauth.SLACK_SCOPES)
        user = dict(id="U1", scope=scopes, token_type="user", access_token="slack")
        who = dict(user_id="U1", team_id="T1")
        denied = oauth.SignInError("Provider declined request.")
        for replies in [
            [dict(access_token="bot", token_type="bot")],
            [dict(authed_user=dict(user, scope="chat:write"))],
            [dict(authed_user=user), dict(who, bot_id="B1")],
            [dict(authed_user=user), dict(who, user_id="U2")],
            [
                dict(authed_user=user),
                who,
                dict(channels=[], response_metadata={"next_cursor": "more"}),
                denied,
            ],
        ]:
            with self.subTest(replies=len(replies)):
                self.begin("slack")
                with self.assertRaises(oauth.SignInError):
                    self.finish(*replies)
                self.assert_closed()

    def test_denial_cancellation_timeout_and_busy_port_release(self):
        attempt = self.begin()
        initial = self.args.copy()
        with patch.object(oauth, "_request") as request, ThreadPoolExecutor() as pool:
            result = pool.submit(attempt.wait, self.cancel, 5)
            self.callback_request({"state": initial["state"], "error": "sensitive provider detail"})
            with self.assertRaisesRegex(oauth.SignInError, "did not grant") as denied:
                result.result(timeout=5)
            self.assertNotIn("sensitive", str(denied.exception))
            request.assert_not_called()
        self.assert_closed()
        attempt = self.begin()
        self.assertNotEqual(initial["state"], self.args["state"])
        self.assertNotEqual(initial["code_challenge"], self.args["code_challenge"])
        with patch.object(oauth, "SLACK_CALLBACK_PORT", self.callback.port):
            with self.assertRaisesRegex(oauth.SignInError, "unavailable"):
                oauth.begin("slack", self.client)
        with self.assertRaisesRegex(oauth.SignInError, "timed out"):
            attempt.wait(self.cancel, 0)
        self.assert_closed()
        attempt = self.begin()
        self.cancel.set()
        with self.assertRaisesRegex(oauth.SignInError, "canceled"):
            attempt.wait(self.cancel)
        self.assert_closed()
        attempt.close()

    def test_refresh_preserves_google_token_and_rotates_slack_token(self):
        for provider in ("google", "slack"):
            with self.subTest(provider=provider):
                credential = dict(
                    self.client,
                    provider=provider,
                    expires_at=1,
                    access_token="old",
                    refresh_token="refresh1",
                )
                grant = dict(access_token="new", expires_in=60)
                if provider == "slack":
                    credential.pop("client_secret")  # Public desktop PKCE client.
                    grant.update(token_type="user", refresh_token="refresh2")
                with (
                    patch.object(oauth, "_request", return_value=grant) as req,
                    patch.object(oauth.time, "time", return_value=100),
                ):
                    result = oauth.refresh(credential)
                self.assertEqual(result["expires_at"], 160)
                self.assertEqual(result["refresh_token"], grant.get("refresh_token", "refresh1"))
                self.assertEqual(result["access_token"], "new")
                self.assertEqual(req.call_args.args, oauth.TOKEN_ENDPOINTS[provider])
                self.assertEqual(req.call_args.kwargs["form"]["grant_type"], "refresh_token")
                self.assertEqual("client_secret" in req.call_args.kwargs["form"], provider == "google")
        non_expiring = dict(provider="slack", access_token="long-lived", expires_at=0, refresh_token="")
        with patch.object(oauth, "_request") as req:
            self.assertEqual(oauth.refresh(non_expiring), non_expiring)
            req.assert_not_called()

    def test_transport_does_not_follow_redirect_or_disclose_provider_body(self):
        for status, body in [(307, {}), (200, dict(ok=False, error="private-message"))]:
            with self.subTest(status=status), patch.object(http.client, "HTTPSConnection") as https:
                conn = https.return_value
                response = conn.getresponse.return_value
                response.status = status
                response.read.return_value = json.dumps(body).encode()
                with self.assertRaises(oauth.SignInError) as exc:
                    oauth._request(*oauth.TOKEN_ENDPOINTS["slack"], form={"code": "private-code"})
                self.assertNotIn("private", str(exc.exception))
                https.assert_called_once()
                conn.request.assert_called_once()
                conn.close.assert_called_once()
