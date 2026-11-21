"""In-memory sign-in attempts. Credentials cross into Keychain only on confirmation."""

import json
import os
import re
import secrets
import threading
import webbrowser
from pathlib import Path

import workspace_bridge
import account_oauth
import connectors
import github_signin
from store import uid

CLIENTS_FILE = Path(__file__).with_name("oauth-clients.json")
KINDS = {"github", "email", "slack"}
LIFETIME = 600


def slack_manifest():
    """Desktop publisher registration for Slack’s app manifest API."""
    return {
        "display_information": {"name": "MeMyselfAI"},
        "oauth_config": {
            "redirect_urls": ["http://localhost:43827/oauth/callback"],
            "scopes": {"user": list(account_oauth.SLACK_SCOPES)},
            "pkce_enabled": True,
        },
        "settings": {"token_rotation_enabled": True},
    }


def registration(kind, fields):
    if not isinstance(fields, dict):
        raise ValueError("Enter a desktop app registration.")
    client = fields.get("client_id")
    pattern = r"[0-9]+\.[0-9]+" if kind == "slack" else r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com"
    if not isinstance(client, str) or not re.fullmatch(pattern, client.strip()):
        if kind == "slack":
            raise ValueError(
                "Copy the numeric Client ID from Basic Information → App Credentials in your Slack app. It looks like 123456789.987654321."
            )
        raise ValueError("Enter the provider's desktop client ID.")
    result = {"client_id": client.strip()}
    if kind == "email" and fields.get("client_secret"):
        secret = fields["client_secret"]
        if not isinstance(secret, str) or not re.fullmatch(r"[!-~]{1,512}", secret):
            raise ValueError("Invalid desktop client secret.")
        result["client_secret"] = secret
    return result


class SignIns:
    def __init__(self, engine):
        self.engine = engine
        self.lock = threading.RLock()
        self.attempts = {}
        # A single desktop process owns the store. Reclaim profiles left by a crash.
        saved = {c.get("workspace_profile") for c in engine.db.rows("connections")}
        for folder in (engine.db.root / "google-accounts").glob("*"):
            if not re.fullmatch(r"[a-f0-9]{32}", folder.name) or folder.is_symlink() or not folder.is_dir():
                continue
            (folder / ".gemini-cli-workspace-master-key").unlink(missing_ok=True)
            if folder.name not in saved and (folder / "gemini-extension.json").is_file():
                workspace_bridge.forget(engine.db.root, folder.name)

    def _registration(self, kind, *, load=False):
        entry = self.engine.db.get("auth_clients", kind)
        if entry:
            return json.loads(connectors._secret(entry["credential"])) if load else True
        if CLIENTS_FILE.is_file():
            try:
                fields = json.loads(CLIENTS_FILE.read_text()).get(kind)
                if fields:
                    return registration(kind, fields) if load else bool(registration(kind, fields))
            except (OSError, TypeError, ValueError, AttributeError):
                raise ValueError("The bundled sign-in registration is invalid.") from None
        if kind == "email":
            client_id = os.environ.get("GOOGLE_WORKSPACE_CLI_CLIENT_ID")
            if client_id:
                client = registration(
                    kind,
                    dict(
                        client_id=client_id,
                        client_secret=os.environ.get("GOOGLE_WORKSPACE_CLI_CLIENT_SECRET", ""),
                    ),
                )
                return client if load else True
            config_dir = Path(
                os.environ.get("GOOGLE_WORKSPACE_CLI_CONFIG_DIR") or Path.home() / ".config/gws"
            )
            config = config_dir / "client_secret.json"
            if config.is_file():
                try:
                    client = registration(kind, json.loads(config.read_text())["installed"])
                except (KeyError, ValueError, OSError):
                    return None
                return client if load else True
        return None

    def action(self, data):
        op = data["op"]
        if op in {"auth_info", "auth_configure", "auth_start"}:
            kind = data.get("kind")
            if kind not in KINDS:
                raise ValueError("Choose an account provider.")
            if op == "auth_info":
                return {
                    "configured": kind == "github" or bool(self._registration(kind)),
                }
            if op == "auth_configure":
                if kind != "email":
                    raise ValueError("Only personal Google clients use in-app registration.")
                client = registration(kind, data)
                with self.engine.db.atomic():
                    record = self.engine.db.get("auth_clients", kind) or {"id": kind, "credential": uid()}
                    connectors.store_secret(record["credential"], json.dumps(client))
                    self.engine.db.put("auth_clients", record)
                return {}
            return self.start(kind, data)
        key = data.get("flow", "")
        with self.lock:
            item = self.attempts.get(key)
            if op == "auth_cancel":
                self._discard(key)
                return {}
            if not item:
                raise ValueError("Sign-in expired. Start again.")
            if op == "auth_status":
                return dict(item["public"])
            if op == "auth_finish":
                return self.finish(key, item, data.get("values", data.get("value")))
        raise ValueError("Unknown sign-in action.")

    def _discard(self, key):
        item = self.attempts.pop(key, None)
        if item:
            item["cancel"].set()
            item["timer"].cancel()
            if item.get("new_profile"):
                workspace_bridge.forget(self.engine.db.root, item["new_profile"])
            item.pop("grant", None)

    def expire(self, key):
        with self.lock:
            self._discard(key)

    def close(self):
        with self.lock:
            for key in list(self.attempts):
                self._discard(key)

    def start(self, kind, data):
        ident = data.get("id")
        previous = self.engine.db.get("connections", ident) if ident else None
        if ident and (not previous or previous["kind"] != kind):
            raise ValueError("Choose the original account to reconnect.")
        method = data.get("method", "browser")
        if method == "slack_token" and kind == "slack":
            token = data.get("secret", "")
            refresh = data.get("refresh_token", "")
            if not isinstance(token, str) or not isinstance(refresh, str):
                raise ValueError("Enter a Slack user access token or refresh token.")
            token, refresh = token.strip(), refresh.strip()
            if token and not re.fullmatch(r"(?:xoxp-|xoxe\.xoxp-)[A-Za-z0-9._-]{1,4096}", token):
                raise ValueError("Enter a Slack user access token beginning with xoxp- or xoxe.xoxp-.")
            if not token and not refresh:
                raise ValueError("Enter a Slack user access token or refresh token.")
            client = dict(provider="slack", access_token=token)
            if refresh:
                if not re.fullmatch(r"xoxe-[A-Za-z0-9._-]{1,4096}", refresh):
                    raise ValueError("Enter a Slack refresh token beginning with xoxe-.")
                issuer = (
                    registration("slack", data)
                    if data.get("client_id")
                    else self._registration("slack", load=True)
                )
                if not issuer:
                    raise ValueError("Enter the client ID of the Slack app that issued this refresh token.")
                client.update(issuer, refresh_token=refresh)
                secret = data.get("client_secret", "")
                if not isinstance(secret, str) or (
                    secret and not re.fullmatch(r"[!-~]{1,512}", secret.strip())
                ):
                    raise ValueError("Invalid Slack client secret.")
                if secret:
                    client["client_secret"] = secret.strip()
        elif method == "gmail_password" and kind == "email":
            address = data.get("address")
            if not isinstance(address, str):
                raise ValueError("Enter your Google email address.")
            config = connectors.validate(
                "email", dict(address=address, imap_host="imap.gmail.com", smtp_host="smtp.gmail.com")
            )
            password = data.get("secret")
            password = password.replace(" ", "") if isinstance(password, str) else ""
            if not re.fullmatch(r"[a-zA-Z0-9]{16}", password):
                raise ValueError("Enter the 16-character app password generated by Google.")
            client = {"address": config["address"], "password": password}
        elif method == "browser":
            client = self._registration(kind, load=True) if kind != "github" else None
        else:
            raise ValueError("Choose a supported sign-in method.")
        if kind == "email" and method == "browser" and not client:
            method = "workspace_connector"
        if kind != "github" and method != "workspace_connector" and not client:
            raise ValueError("Set up this provider's app registration first.")
        key = secrets.token_urlsafe(24)
        timer = threading.Timer(LIFETIME, self.expire, (key,))
        timer.daemon = True
        item = dict(
            kind=kind,
            method=method,
            previous=previous,
            cancel=threading.Event(),
            timer=timer,
            public={"phase": "waiting", "message": "Checking account…"},
        )
        with self.lock:
            if len(self.attempts) >= 8:
                raise ValueError("Finish or cancel another sign-in first.")
            if method == "workspace_connector":
                item["new_profile"] = uid()
                workspace_bridge.make_profile(self.engine.db.root, item["new_profile"])
            self.attempts[key] = item
            timer.start()
        threading.Thread(
            target=self.authorize, args=(key, item, client), daemon=True, name="account-sign-in"
        ).start()
        return {"flow": key}

    def authorize(self, key, item, client):
        def progress(url=None, code=None, message=None):
            with self.lock:
                if key in self.attempts:
                    item["public"] = {
                        "phase": "waiting",
                        **{k: v for k, v in dict(url=url, code=code, message=message).items() if v},
                    }

        def check_active():
            if item["cancel"].is_set():
                raise ValueError("Connection cancelled.")

        try:
            if item["method"] == "workspace_connector":
                progress(
                    message="Choose your account in Google’s browser window and allow the Google Workspace extension to connect."
                )
                with workspace_bridge.session(
                    self.engine.db.root, item["new_profile"], item["cancel"]
                ) as client:
                    address = workspace_bridge.address(client, LIFETIME)
                    services = workspace_bridge.accessible_services(client)
                grant = dict(
                    account=address,
                    identity=address,
                    options=[dict(value=address, label=address)],
                    services=services,
                )
            elif item["method"] == "slack_token":
                credential = client
                needs_exchange = not client["access_token"]
                if not needs_exchange:
                    try:
                        account, identity, options = account_oauth._slack_choices(
                            client["access_token"], None, check_active
                        )
                    except account_oauth.ExpiredSlackToken:
                        if not client.get("refresh_token"):
                            raise
                        needs_exchange = True
                if needs_exchange:
                    check_active()
                    credential = account_oauth.refresh(client)
                    account, identity, options = account_oauth._slack_choices(
                        credential["access_token"], None, check_active
                    )
                grant = dict(account=account, identity=identity, options=options, credential=credential)
                if client.get("refresh_token") and not needs_exchange:
                    grant["verify_rotation"] = True
            elif item["method"] == "gmail_password":
                address = client["address"]
                connectors.verify_gmail_password(address, client["password"])
                grant = dict(
                    account=address,
                    identity=address,
                    options=[dict(value=address, label=address)],
                    credential=dict(provider="google", app_password=client["password"]),
                )
            elif item["kind"] == "github":
                grant = github_signin.authorize(item["cancel"], progress)
            else:
                handle = account_oauth.begin(item["kind"], client)
                try:
                    progress(url=handle.url, message="Continue in your browser.")
                    try:
                        webbrowser.open(handle.url)
                    except (OSError, webbrowser.Error):
                        pass  # The waiting screen also has a link to the consent page.
                    grant = handle.wait(item["cancel"], timeout=LIFETIME)
                finally:
                    handle.close()
            with self.lock:
                if key in self.attempts:
                    item["grant"] = grant
                    item["public"] = {
                        "phase": "ready",
                        "account": grant["account"],
                        "options": grant["options"],
                        **({"services": grant["services"]} if "services" in grant else {}),
                    }
        except (ValueError, RuntimeError, OSError) as exc:
            with self.lock:
                if key in self.attempts:
                    self.fail(item, str(exc))
        except Exception:
            with self.lock:
                if key in self.attempts:
                    self.fail(item, "Sign-in failed. Please try again.")

    def fail(self, item, message):
        if item.get("new_profile"):
            workspace_bridge.forget(self.engine.db.root, item.pop("new_profile"))
        if item.get("previous"):
            self.engine.db.log("Sign-in failed", message, connection=item["previous"]["id"])
        item["public"] = {"phase": "error", "message": message}

    def finish(self, key, item, value):
        grant = item.get("grant")
        if not grant:
            raise ValueError("Complete sign-in first.")
        if item["kind"] == "github":
            if value:
                raise ValueError("GitHub connects the entire CLI account.")
            self.engine.connect_github_account(grant["account"], item["previous"])
            self._discard(key)
            self.engine.wake.set()
            return {}
        if item["kind"] == "slack":
            if grant.get("verify_rotation"):
                previous = item.get("previous")
                if previous and previous.get("identity") != grant["identity"]:
                    raise ValueError("Reconnect the original Slack workspace.")
                # A pasted access token has no known remaining lifetime. Exchange once on
                # confirmation, and retain the new pair if identity verification needs a retry.
                if not grant["credential"].get("expires_at"):
                    grant["credential"] = account_oauth.refresh(grant["credential"])
                _, identity = account_oauth.slack_identity(grant["credential"]["access_token"])
                if identity != grant["identity"]:
                    raise ValueError("The Slack access and refresh tokens belong to different accounts.")
                del grant["verify_rotation"]
            self.save_slack_channels(item, grant, value)
            self._discard(key)
            self.engine.wake.set()
            return {}
        selected = next((o for o in grant["options"] if o["value"] == value), None)
        if not selected:
            raise ValueError("Select an available source.")
        kind = item["kind"]
        identity = grant.get("identity") or grant["account"]
        method = "google"
        if identity.casefold() != value.casefold():
            raise ValueError("Choose the verified email address.")
        config = connectors.validate(
            kind, dict(address=value, imap_host="imap.gmail.com", smtp_host="smtp.gmail.com", mailbox="INBOX")
        )
        profile = item.get("new_profile")
        label = value
        previous = item["previous"]
        # Finish and reconnect serialize with manual reconfiguration and disconnect.
        with self.engine.dispatch_lock, self.engine.db.atomic():
            if not previous:
                previous = next(
                    (
                        c
                        for c in self.engine.db.rows("connections")
                        if c["kind"] == kind
                        and c.get("identity", "").casefold() == identity.casefold()
                        and c.get("auth_method") in {"google", "workspace_connector"}
                    ),
                    None,
                )
            if previous:
                current = self.engine.db.get("connections", previous["id"])
                if not current or current.get("revision") != previous.get("revision"):
                    raise ValueError("Connection changed during sign-in. Start again.")
                if current.get("identity") and current["identity"].casefold() != identity.casefold():
                    raise ValueError("Sign in to the original account to reconnect.")
                if kind == "email":
                    old = current["config"]
                    if (
                        old["address"].casefold() != value.casefold()
                        or old["imap_host"] != "imap.gmail.com"
                        or old["smtp_host"] != "smtp.gmail.com"
                    ):
                        raise ValueError("Add this mailbox as a separate connection.")
                    config = old
                label = current["label"]
                identity = current.get("identity") or identity
            credential = (
                ""
                if profile
                else connectors.OAUTH_PREFIX + json.dumps({**grant["credential"], "identity": identity})
            )
            record = {
                "kind": kind,
                "label": label,
                "config": config,
                "secret": credential,
            }
            if previous:
                record["id"] = previous["id"]
            self.engine.connect(
                record,
                verified={
                    "auth_method": "workspace_connector" if profile else method,
                    **({"workspace_profile": profile} if profile else {}),
                    "identity": identity,
                    "google_services": grant.get("services", ["Gmail"]),
                },
            )
            changed_transport = previous and bool(previous.get("workspace_profile")) != bool(profile)
            if changed_transport:
                for task in self.engine.db.rows("tasks"):
                    if (
                        task.get("google") == previous["id"]
                        and task["status"] == "review"
                        and task.get("operation") == "calendar_event"
                    ):
                        task.update(status="queued", draft="", calendar_choice=identity)
                        self.engine.save(
                            task, "Google connection upgraded. Preparing a new calendar preview."
                        )
            if "Calendar" in grant.get("services", []):
                for t in self.engine.db.rows("tasks"):
                    if t["kind"] == "commitment" and t["status"] == "scheduled":
                        self.engine.schedule_deadline(
                            t["id"], t["project"], t.get("event", ""), t["title"], t.get("due")
                        )
        if profile:
            item.pop("new_profile")
        # Keep the old profile usable until the new connection and its previews commit.
        if previous and previous.get("workspace_profile"):
            workspace_bridge.forget(self.engine.db.root, previous["workspace_profile"])
        elif profile and previous and not previous.get("credential_key"):
            connectors.delete_secret(previous["id"])
        self._discard(key)
        self.engine.wake.set()
        return {}

    def save_slack_channels(self, item, grant, values):
        if values is None:
            previous = item["previous"]
            identity = grant["identity"]
            key = "slack-oauth:" + identity
            with self.engine.dispatch_lock, self.engine.db.atomic():
                if previous:
                    saved = self.engine.db.get("connections", previous["id"])
                    if (
                        not saved
                        or saved["revision"] != previous["revision"]
                        or saved.get("identity") != identity
                    ):
                        raise ValueError("Reconnect the original Slack workspace.")
                parent = self.workspace(identity, key, grant["account"])
                self.reconcile_channels(parent, grant["options"])
                connectors.store_secret(
                    parent["credential_key"],
                    connectors.OAUTH_PREFIX + json.dumps({**grant["credential"], "identity": identity}),
                )
            return
        if isinstance(values, str):
            values = [values]  # Older desktop windows submit a single channel.
        options = {o["value"]: o["label"] for o in grant["options"]}
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(v, str) or v not in options for v in values)
        ):
            raise ValueError("Choose at least one channel from this Slack account.")
        channels = list(dict.fromkeys(values))
        identity = grant["identity"]
        key = "slack-oauth:" + identity
        verified = dict(auth_method="slack", identity=identity, credential_key=key)
        previous = item["previous"]
        old_keys = []
        with self.engine.dispatch_lock:
            with self.engine.db.atomic():
                existing = {
                    c["config"]["channel"]: c
                    for c in self.engine.db.rows("connections")
                    if c["kind"] == "slack"
                    and c.get("auth_method") == "slack"
                    and c.get("identity") == identity
                }
                if previous:
                    current = self.engine.db.get("connections", previous["id"])
                    if not current or current["revision"] != previous["revision"]:
                        raise ValueError("Connection changed during sign-in. Start again.")
                    if previous.get("identity") and previous["identity"] != identity:
                        raise ValueError("Sign in to the original account to reconnect.")
                    if channels != [previous["config"]["channel"]]:
                        raise ValueError("Reconnect the original channel.")
                    existing[channels[0]] = current
                for channel in channels:
                    old = existing.get(channel)
                    record = dict(kind="slack", config={"channel": channel}, label=options[channel])
                    if old:
                        record.update(id=old["id"], label=old["label"])
                        if not old.get("credential_key"):
                            old_keys.append(old["id"])
                    self.engine.connect(record, verified=verified)
                connectors.store_secret(
                    key, connectors.OAUTH_PREFIX + json.dumps({**grant["credential"], "identity": identity})
                )
            for old_key in old_keys:
                connectors.delete_secret(old_key)

    def workspace(self, identity, key, label):
        ident = "slack-workspace:" + identity
        saved = self.engine.db.get("connections", ident)
        if saved:
            key = saved["credential_key"]
        parent = dict(
            id=ident,
            kind="slack",
            auth_method="slack_workspace",
            identity=identity,
            config={},
            label=label,
            credential_key=key,
            status="Connected",
            next=0,
            revision=uid(),
        )
        self.engine.db.put("connections", parent)
        self.engine.db.log("Slack workspace connected", label, connection=ident)
        return parent

    def reconcile_channels(self, parent, options):
        db = self.engine.db
        saved = db.get("connections", parent["id"])
        if not saved or saved["revision"] != parent["revision"]:
            return
        existing = {
            c["config"]["channel"]: c
            for c in db.rows("connections")
            if c.get("auth_method") == "slack" and c.get("identity") == parent["identity"]
        }
        for option in options:
            channel = option["value"]
            child = existing.pop(channel, None)
            if child is None:
                child = dict(
                    id=uid(),
                    kind="slack",
                    config={"channel": channel},
                    identity=parent["identity"],
                    auth_method="slack",
                    next=0,
                    status="Connecting",
                    revision=uid(),
                )
            if child.pop("unavailable", False) or child.get("slack_parent") != parent["id"]:
                child.update(next=0, revision=uid())
            child.update(
                slack_parent=parent["id"], credential_key=parent["credential_key"], label=option["label"]
            )
            db.put("connections", child)
        for child in existing.values():
            if not child.get("unavailable"):
                child.update(
                    slack_parent=parent["id"],
                    unavailable=True,
                    status="Channel access removed",
                    revision=uid(),
                )
                db.put("connections", child)
        db.log("Channel sync", f"{len(options)} channels", connection=saved["id"])
        saved.update(next=self.engine.now() + 900, status="Connected", channel_count=len(options))
        db.put("connections", saved)

    def refresh_workspace(self, parent):
        token = connectors._transport_secret(dict(parent, auth_method="slack"), {})
        _, identity, options = account_oauth._slack_choices(
            token, parent["identity"].split("/")[-1], lambda: None
        )
        if parent["identity"] != identity:
            raise ValueError("Slack workspace identity changed.")
        with self.engine.dispatch_lock, self.engine.db.atomic():
            self.reconcile_channels(parent, options)
