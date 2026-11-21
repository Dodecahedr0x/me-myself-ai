"""Native OAuth for the Gmail and Slack connectors. Credentials are returned to the service only."""

import base64
import hashlib
import http.client
import json
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode

GOOGLE_MAIL = "https://mail.google.com/"
GOOGLE_SCOPES = {
    "Gmail": GOOGLE_MAIL,
    "Calendar": "https://www.googleapis.com/auth/calendar.events.owned",
    "Drive": "https://www.googleapis.com/auth/drive.readonly",
    "Tasks": "https://www.googleapis.com/auth/tasks.readonly",
    "Contacts": "https://www.googleapis.com/auth/contacts.readonly",
}
SLACK_SCOPES = ("channels:read", "groups:read", "channels:history", "groups:history", "chat:write")
SLACK_CALLBACK_PORT = 43827
CALLBACK_PATH = "/oauth/callback"
TOKEN_ENDPOINTS = {
    "google": ("oauth2.googleapis.com", "/token"),
    "slack": ("slack.com", "/api/oauth.v2.access"),
}


class SignInError(ValueError):
    """A message safe to display in the desktop application."""


class ExpiredSlackToken(SignInError):
    """Slack explicitly reported that the supplied access token expired."""


def _request(host, path, *, form=None, token=None):
    # HTTPSConnection never follows redirects, including redirects on a token exchange.
    conn = http.client.HTTPSConnection(host, timeout=20)
    headers = {"Accept": "application/json", "User-Agent": "memyselfai-desktop"}
    body = None
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(form)
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        conn.request("POST" if form is not None else "GET", path, body, headers)
        response = conn.getresponse()
        if response.status != 200:
            raise SignInError("The provider declined the request. Check app access and sign in again.")
        result = json.loads(response.read(4 * 1024 * 1024))
    except SignInError:
        raise
    except (http.client.HTTPException, OSError, ValueError):
        raise SignInError(
            "Could not read the provider response. Check the connection and try again."
        ) from None
    finally:
        conn.close()
    if host == "slack.com" and isinstance(result, dict):
        if result.get("error") == "token_expired":
            raise ExpiredSlackToken(
                "Slack access token expired. Enter its refresh token or a current user token."
            )
        if result.get("error") in {"bad_client_secret", "invalid_client_id", "bad_client_id"}:
            raise SignInError(
                "Check the client ID and client secret of the Slack app that issued the refresh token."
            )
        if result.get("error") in {"invalid_refresh_token", "bad_refresh_token"}:
            raise SignInError("Slack declined this refresh token. Enter the latest one issued for this user.")
        if result.get("error") in {"invalid_auth", "token_revoked"}:
            raise SignInError("Slack rejected this credential. Try a current user token or sign in again.")
        if result.get("error") == "missing_scope":
            raise SignInError(
                "Slack needs permission to list joined channels. Check the channels:read and groups:read user scopes."
            )
    if not isinstance(result, dict) or (host == "slack.com" and result.get("ok") is not True):
        raise SignInError("The provider declined the request. Check app access and sign in again.")
    return result


class _Callback(BaseHTTPRequestHandler):
    timeout = 1

    def log_message(self, *args):
        # The request line contains a short-lived authorization code.
        pass

    def _reply(self, status, message):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(message.encode())

    def send_error(self, code, message=None, explain=None):
        self._reply(code, "Invalid sign-in request.")

    def do_GET(self):
        path, _, query = self.path.partition("?")
        server = self.server
        if path != CALLBACK_PATH or self.headers.get_all("Host") != [server.authority]:
            self._reply(404, "Unknown callback.")
            return
        try:
            fields = parse_qs(query, keep_blank_values=True, max_num_fields=32)
        except ValueError:
            fields = {}
        state = fields.get("state", [])
        if len(state) != 1 or not secrets.compare_digest(state[0].encode(), server.state.encode()):
            self._reply(400, "Invalid sign-in request.")
            return
        if fields.get("error"):
            server.denied = True
        else:
            code = fields.get("code", [])
            if len(code) != 1 or not code[0]:
                self._reply(400, "Missing authorization code.")
                return
            server.code = code[0]
        self._reply(200, "Return to MeMyselfAI to finish.")


class _Listener(HTTPServer):
    # Use the numeric loopback address even when Slack requires localhost in the URL.
    def handle_error(self, request, client_address):
        # A disconnected browser can interrupt a response.
        pass


class _Attempt:
    def __init__(self, provider, registration):
        self.provider = provider
        self.client = {"client_id": registration["client_id"]}
        if provider == "google" and registration.get("client_secret"):
            self.client["client_secret"] = registration["client_secret"]
        self.verifier = secrets.token_urlsafe(48)
        proof = hashlib.sha256(self.verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(proof).rstrip(b"=").decode("ascii")
        port = SLACK_CALLBACK_PORT if provider == "slack" else 0
        try:
            self.server = _Listener(("127.0.0.1", port), _Callback)
        except OSError:
            raise SignInError(
                "The local sign-in port is unavailable. Close any other sign-in and retry."
            ) from None
        server = self.server
        server.timeout = 0.2
        name = "localhost" if provider == "slack" else "127.0.0.1"
        server.authority = f"{name}:{server.server_port}"
        server.state = secrets.token_urlsafe(32)
        server.code = None
        server.denied = False
        self.redirect_uri = f"http://{server.authority}{CALLBACK_PATH}"
        args = dict(
            client_id=self.client["client_id"],
            redirect_uri=self.redirect_uri,
            state=server.state,
            code_challenge=challenge,
            code_challenge_method="S256",
        )
        if provider == "slack":
            endpoint = "https://slack.com/oauth/v2/authorize"
            args.update(scope="", user_scope=",".join(SLACK_SCOPES))
        else:
            endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
            args.update(
                scope="openid email " + " ".join(GOOGLE_SCOPES.values()),
                response_type="code",
                access_type="offline",
                prompt="consent select_account",
            )
        self.url = endpoint + "?" + urlencode(args)

    def close(self):
        self.server.server_close()

    def wait(self, cancel_event, timeout=600):
        deadline = time.monotonic() + timeout

        def check_active():
            if cancel_event.is_set():
                raise SignInError("Sign-in canceled.")
            if time.monotonic() >= deadline:
                raise SignInError("Sign-in timed out. Start again.")

        server = self.server
        try:
            while not server.code and not server.denied:
                check_active()
                server.handle_request()
        finally:
            self.close()
        check_active()
        if server.denied:
            raise SignInError("The provider did not grant access. Start again to connect.")
        form = dict(
            self.client, code=server.code, redirect_uri=self.redirect_uri, code_verifier=self.verifier
        )
        if self.provider == "google":
            form["grant_type"] = "authorization_code"
        result = _request(*TOKEN_ENDPOINTS[self.provider], form=form)
        if self.provider == "slack":
            grant = result.get("authed_user") or {}
            scopes = set(grant.get("scope", "").split(","))
            if grant.get("token_type") != "user" or not grant.get("id"):
                raise SignInError("Slack did not authorize a user account. Check the app's user scopes.")
            if not scopes.issuperset(SLACK_SCOPES):
                raise SignInError(
                    "Slack did not grant all channel and reply permissions. Check the app's user scopes."
                )
        else:
            grant = result
            allowed = grant.get("scope", " ".join(GOOGLE_SCOPES.values())).split()
            services = [name for name, scope in GOOGLE_SCOPES.items() if scope in allowed]
            if not services:
                raise SignInError(
                    "Google Workspace access was declined. Select the services you want to connect."
                )
        cred = _credential(self.provider, self.client, grant)
        check_active()
        if self.provider == "slack":
            account, identity, options = _slack_choices(cred["access_token"], grant["id"], check_active)
        else:
            account, options = _google_choice(cred["access_token"])
            identity = account
        check_active()
        result = {"account": account, "identity": identity, "options": options, "credential": cred}
        if self.provider == "google":
            result["services"] = services
        return result


def begin(kind, registration):
    if kind not in ("slack", "email") or not registration.get("client_id"):
        raise SignInError("Configure the provider's desktop app registration before signing in.")
    return _Attempt("google" if kind == "email" else "slack", registration)


def _google_choice(token):
    identity = _request("openidconnect.googleapis.com", "/v1/userinfo", token=token)
    email = identity.get("email")
    if (
        identity.get("email_verified") is not True
        or not isinstance(email, str)
        or not email.isprintable()
        or not re.fullmatch(r"[^@\s<>]+@[^@\s<>]+", email)
    ):
        raise SignInError("Google could not verify the account's email address.")
    return email, [{"value": email, "label": email}]


def slack_identity(token, user_id=None):
    identity = _request("slack.com", "/api/auth.test", token=token)
    actual_user = identity.get("user_id")
    if (
        identity.get("bot_id")
        or not actual_user
        or (user_id is not None and actual_user != user_id)
        or not identity.get("team_id")
    ):
        raise SignInError("Slack could not verify the signed-in workspace and user.")
    account = f"{identity.get('user') or actual_user} ({identity.get('team') or identity['team_id']})"
    return account, identity["team_id"] + "/" + actual_user


def _slack_choices(token, user_id, check_active):
    account, identity = slack_identity(token, user_id)
    channels = {}
    params = dict(types="public_channel,private_channel", exclude_archived="true", limit=200)
    cursors = set()
    while True:
        check_active()
        page = _request("slack.com", "/api/conversations.list?" + urlencode(params), token=token)
        if not isinstance(page.get("channels"), list):
            raise SignInError("Slack could not list the account's channels. Try signing in again.")
        for ch in page["channels"]:
            if ch.get("is_archived") or (ch.get("is_private") and ch.get("is_member") is False):
                continue
            if re.fullmatch(r"[CG][A-Z0-9]+", ch.get("id", "")) and ch.get("name"):
                channels[ch["id"]] = "#" + ch["name"]
        cursor = (page.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
        if cursor in cursors:
            raise SignInError("Slack returned an incomplete channel list. Try again.")
        cursors.add(cursor)
        params["cursor"] = cursor
    options = [
        {"value": key, "label": name} for key, name in sorted(channels.items(), key=lambda pair: pair[1])
    ]
    return account, identity, options


def _credential(provider, client, grant, previous_refresh=""):
    token = grant.get("access_token")
    refresh_token = grant.get("refresh_token") or previous_refresh
    expiry = grant.get("expires_in")
    if not isinstance(token, str) or not token:
        raise SignInError("The provider did not return an access token. Sign in again.")
    if (provider == "google" or expiry is not None) and not refresh_token:
        raise SignInError("The provider did not grant offline access. Sign in again and allow access.")
    if refresh_token and (not isinstance(expiry, int) or expiry <= 0):
        raise SignInError("The provider did not return a valid expiry. Sign in again.")
    return dict(
        client,
        provider=provider,
        access_token=token,
        refresh_token=refresh_token,
        expires_at=int(time.time()) + expiry if expiry else 0,
    )


def refresh(credential):
    """Exchange an expiring credential. The caller persists the full returned record."""
    provider = credential.get("provider")
    if provider not in TOKEN_ENDPOINTS:
        raise SignInError("Unknown sign-in provider. Reconnect the account.")
    if not credential.get("refresh_token"):
        if provider == "slack" and not credential.get("expires_at") and credential.get("access_token"):
            return dict(credential)
        raise SignInError("Sign in again to renew access to this account.")
    client = {"client_id": credential["client_id"]}
    if credential.get("client_secret"):
        client["client_secret"] = credential["client_secret"]
    form = dict(client, grant_type="refresh_token", refresh_token=credential["refresh_token"])
    grant = _request(*TOKEN_ENDPOINTS[provider], form=form)
    if provider == "slack":
        grant = grant.get("authed_user") or grant
        if grant.get("token_type") != "user" or not grant.get("refresh_token"):
            raise SignInError("Slack did not renew the user credentials. Sign in again.")
    return _credential(provider, client, grant, credential["refresh_token"])
