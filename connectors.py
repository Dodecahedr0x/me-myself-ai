"""Provider transports. Only the desktop executor calls send()."""

import ctypes
import datetime as dt
import imaplib
import json
import os
import re
import smtplib
import ssl
import sys
import time
import threading

import account_oauth
import github_signin
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class Rejected(RuntimeError):
    """Delivery definitely did not occur."""


def field(key, label, default=None, placeholder=None, required=True):
    return dict(key=key, label=label, default=default, placeholder=placeholder, required=required)


SCHEMAS = {
    "email": {
        "label": "Email",
        "secret_label": "Mail password or app password",
        "help": "Use an account with IMAP and authenticated SMTP submission enabled.",
        "fields": [
            field("address", "Email address"),
            field("imap_host", "IMAP host"),
            field("imap_port", "IMAP TLS port", "993"),
            field("smtp_host", "SMTP host"),
            field("smtp_port", "SMTP submission port", "465"),
            field("mailbox", "Mailbox", "INBOX"),
            field("sent_mailbox", "Sent mailbox", placeholder="Optional", required=False),
        ],
    },
    "slack": {
        "label": "Slack",
        "secret_label": "Slack user token",
        "help": (
            "Copy the channel link in Slack. The ID follows /archives/ in the link. "
            "In your installed Slack app, open OAuth & Permissions and copy the User OAuth Token. "
            "It needs chat:write and either channels:history for a public channel or groups:history for a private channel."
        ),
        "fields": [field("channel", "Channel ID", placeholder="C0123456789")],
    },
    "github": {
        "label": "GitHub",
        "secret_label": "GitHub personal access token",
        "help": "Grant access to Issues and Pull requests in the selected repository.",
        "fields": [field("repository", "Repository", placeholder="owner/repository")],
    },
}

_ADDRESS = re.compile(r"[^\s@,;<>\x00-\x1f\x7f]+@[^\s@,;<>\x00-\x1f\x7f]+")


def validate(kind, config):
    if kind not in SCHEMAS or not isinstance(config, dict):
        raise ValueError("Select a supported provider and its settings.")
    result = {}
    for item in SCHEMAS[kind]["fields"]:
        value = config.get(item["key"])
        value = str(value if value not in (None, "") else item["default"] or "").strip()
        if item["required"] and not value:
            raise ValueError(f"{item['label']} is required.")
        if len(value) > 255 or re.search(r"[\x00-\x1f\x7f]", value):
            raise ValueError(f"Invalid {item['label']}.")
        result[item["key"]] = value
    if kind == "github" and not re.fullmatch(r"[\w.-]+/[\w.-]+", result["repository"], re.ASCII):
        raise ValueError("Enter a GitHub owner/repository.")
    if kind == "github" and any(p in (".", "..") for p in result["repository"].split("/")):
        raise ValueError("Invalid repository name.")
    if kind == "slack" and not re.fullmatch(r"[CGD][A-Z0-9]+", result["channel"]):
        raise ValueError("Enter a Slack conversation ID.")
    if kind == "email":
        if not _ADDRESS.fullmatch(result["address"]):
            raise ValueError("Enter one email address.")
        for key in ("imap_host", "smtp_host"):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", result[key]):
                raise ValueError("Enter a mail server hostname.")
        if not result["imap_port"].isdigit() or not 0 < int(result["imap_port"]) <= 65535:
            raise ValueError("Invalid IMAP TLS port.")
        if result["smtp_port"] not in ("465", "587"):
            raise ValueError("Use SMTP port 465 (TLS) or 587 (STARTTLS).")
    return result


# Security.framework's generic-password functions keep credentials out of argv and pipes.
class _Keychain:
    service = b"memyselfai.connectors"

    def __init__(self):
        self.api = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        p, n = ctypes.c_void_p, ctypes.c_uint32
        ptr = ctypes.POINTER
        signatures = {
            "SecKeychainFindGenericPassword": [p, n, p, n, p, ptr(n), ptr(p), ptr(p)],
            "SecKeychainAddGenericPassword": [p, n, p, n, p, n, p, ptr(p)],
            "SecKeychainItemModifyAttributesAndData": [p, p, n, p],
            "SecKeychainItemDelete": [p],
            "SecKeychainItemFreeContent": [p, p],
        }
        for name, args in signatures.items():
            fn = getattr(self.api, name)
            fn.argtypes = args
            fn.restype = ctypes.c_int32
        self.cf.CFRelease.argtypes = [p]
        self.cf.CFRelease.restype = None

    def access(self, operation, identity, secret=None):
        account = str(identity).encode()
        prefix = (None, len(self.service), self.service, len(account), account)
        size, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        read = operation == "read"
        status = self.api.SecKeychainFindGenericPassword(
            *prefix,
            ctypes.byref(size) if read else None,
            ctypes.byref(data) if read else None,
            ctypes.byref(item),
        )
        try:
            if operation == "store":
                encoded = secret.encode()
                if status == -25300:  # errSecItemNotFound
                    status = self.api.SecKeychainAddGenericPassword(*prefix, len(encoded), encoded, None)
                elif status == 0:
                    status = self.api.SecKeychainItemModifyAttributesAndData(
                        item, None, len(encoded), encoded
                    )
            elif operation == "delete":
                if status == -25300:
                    return
                if status == 0:
                    status = self.api.SecKeychainItemDelete(item)
            if status:
                raise RuntimeError(f"Keychain unavailable for this connection (status {status}).")
            if read:
                return ctypes.string_at(data, size.value).decode()
        finally:
            if data:
                self.api.SecKeychainItemFreeContent(None, data)
            if item:
                self.cf.CFRelease(item)


_vault = None


def configure_secrets(root):
    """Use the encrypted file store where macOS Keychain is unavailable, such as on a Linux VPS."""
    global _vault
    if sys.platform == "darwin" and not os.environ.get("MEMYSELFAI_FILE_VAULT"):
        return None
    import vault

    _vault = vault.FileVault(root)
    return _vault.source


def _store():
    if _vault is not None:
        return _vault
    try:
        return _Keychain()
    except OSError as exc:
        raise RuntimeError("The credential store is not configured on this instance.") from exc


# Set by the sync layer so logins reach paired instances, sealed with the workspace key.
secret_listener = None

OAUTH_PREFIX = "memyselfai:oauth:v1:"
_key_locks = [threading.RLock() for _ in range(32)]


def _key_lock(identity):
    return _key_locks[hash(identity) % len(_key_locks)]


def store_secret(identity, secret):
    if not isinstance(secret, str) or not secret or re.search(r"[\x00\r\n]", secret):
        raise ValueError("Enter a valid credential.")
    with _key_lock(identity):
        _store().access("store", identity, secret)
    if secret_listener:
        secret_listener(identity, secret)


def delete_secret(identity):
    with _key_lock(identity):
        _store().access("delete", identity)
    if secret_listener:
        secret_listener(identity, None)


def _secret(identity):
    return _store().access("read", identity)


# A GitHub CLI login copied into the app's store by the instance that has it, so peers can use it.
GITHUB_CLI_LOGIN = "github-cli:"


def github_credential(conn):
    if conn.get("unavailable"):
        raise Rejected("This repository is no longer available to the connected GitHub account.")
    if conn.get("github_parent") or conn.get("auth_method") in {"gh_account", "gh_repository"}:
        try:
            return github_signin.token_for(conn["identity"])
        except RuntimeError:
            # No GitHub CLI login here, as on a VPS: use the login synced from the instance that has one.
            try:
                synced = _secret(GITHUB_CLI_LOGIN + conn["identity"].casefold())
            except Exception:
                synced = None
            if synced:
                return synced
            raise
    return _secret(conn["id"])


def github_repositories(account):
    token = github_credential(account)
    user = _gh("/user", token)[0]["login"]
    if user.casefold() != account["identity"].casefold():
        raise Rejected("The GitHub CLI account differs from the connected account.")
    repos = {}
    page = 1
    while True:
        rows, headers = _gh(
            "/user/repos",
            token,
            dict(
                affiliation="owner,collaborator,organization_member",
                per_page=100,
                page=page,
                sort="full_name",
            ),
        )
        for r in rows:
            repo = validate("github", {"repository": r["full_name"]})["repository"]
            repos[repo.casefold()] = repo
        if 'rel="next"' not in headers.get("Link", ""):
            return list(repos.values())
        page += 1


def _transport_secret(conn, config):
    """Refresh a rotating grant once and bind its use to the verified provider."""
    key = conn.get("credential_key", conn["id"])
    with _key_lock(key):
        raw = github_credential(conn) if conn["kind"] == "github" else _secret(key)
        method = conn.get("auth_method")
        if not raw.startswith(OAUTH_PREFIX):
            if method in {"google", "slack"}:
                raise ValueError("Reconnect this account to renew sign-in.")
            return raw
        grant = json.loads(raw[len(OAUTH_PREFIX) :])
        provider = {"email": "google", "slack": "slack"}.get(conn["kind"])
        if (
            not provider
            or provider != method
            or provider != grant.get("provider")
            or conn.get("identity") != grant.get("identity")
        ):
            raise ValueError("Connection credentials changed. Reconnect this account.")
        if provider == "google":
            if (
                config["address"].casefold() != grant["identity"].casefold()
                or config["imap_host"] != "imap.gmail.com"
                or config["smtp_host"] != "smtp.gmail.com"
                or config["imap_port"] != "993"
                or config["smtp_port"] not in ("465", "587")
            ):
                raise ValueError("Google sign-in requires the verified Gmail mailbox and Google servers.")
            if "app_password" in grant:
                config["gmail_password"] = True
                return grant["app_password"]
            config["xoauth2"] = True
        expires = grant.get("expires_at", 0)
        if expires and expires <= time.time() + 90:
            grant = {**account_oauth.refresh(grant), "identity": grant["identity"]}
            store_secret(key, OAUTH_PREFIX + json.dumps(grant))
        return grant["access_token"]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http(url, token, params=None, body=None, method=None):
    if params:
        url += "?" + urlencode(params)
    req = Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "memyselfai-desktop",
        },
    )
    for attempt in range(3):
        try:
            with build_opener(_NoRedirect()).open(req, timeout=30) as response:
                return json.load(response), response.headers
        except HTTPError as e:
            wait = e.headers.get("Retry-After", "")
            e.close()
            if (
                req.get_method() == "GET"
                and e.code == 429
                and attempt < 2
                and str(wait).isdigit()
                and 0 <= int(wait) <= 120
            ):
                time.sleep(max(1, int(wait)))
                continue
            delay = f" Retry after {wait} seconds." if str(wait).isdigit() else ""
            error = Rejected if e.code in (400, 401, 403, 404, 405, 410, 413, 422, 429) else RuntimeError
            raise error(f"Provider returned HTTP {e.code}.{delay}") from None


def _slack(method, token, params=None, body=None):
    result, _ = _http("https://slack.com/api/" + method, token, params, body)
    if result.get("ok") is not True:
        reason = str(result.get("error", "unknown_error"))
        if not re.fullmatch(r"[a-z_]+", reason):
            reason = "provider_error"
        rejected = {
            "channel_not_found",
            "not_in_channel",
            "is_archived",
            "missing_scope",
            "invalid_auth",
            "token_revoked",
            "not_authed",
            "account_inactive",
            "msg_too_long",
            "no_text",
            "ratelimited",
            "invalid_arguments",
            "thread_not_found",
            "restricted_action",
            "restricted_action_read_only_channel",
        }
        error = Rejected if reason in rejected else RuntimeError
        raise error("Slack returned " + reason + ".")
    return result


def _slack_pages(method, token, params):
    seen = set()
    while True:
        result = _slack(method, token, params)
        yield from result["messages"]
        cursor = result.get("response_metadata", {}).get("next_cursor", "")
        if not cursor:
            if result.get("has_more") or result.get("is_limited"):
                raise RuntimeError("Slack reported incomplete conversation history.")
            return
        if cursor in seen:
            raise RuntimeError("Slack repeated a page cursor.")
        seen.add(cursor)
        params = dict(params, cursor=cursor)


def _slack_identity(token):
    result = _slack("auth.test", token)
    return result["team_id"] + "/" + result["user_id"]


def _slack_events(config, token, start, end):
    identity = _slack("auth.test", token)
    team, user = identity["team_id"], identity["user_id"]
    account = team + "/" + user
    workspace = urlsplit(identity.get("url", "")).hostname or ""
    channel = config["channel"]

    def event(message, parent):
        ts, body = message["ts"], message.get("text", "")
        dest = channel + " / thread " + parent
        return dict(
            key=f"{team}/{channel}/{ts}",
            timestamp=float(ts),
            title=body.splitlines()[0][:120] if body.strip() else "Slack message",
            body=body,
            account=account,
            authored=message.get("user") == user,
            owned=False,
            destination=dest,
            url=(
                f"https://{workspace}/archives/{channel}/p{ts.replace('.', '')}"
                if workspace.endswith(".slack.com")
                else f"https://app.slack.com/client/{team}/{channel}"
            ),
            context={"thread": parent, "channel": channel, "author": message.get("user", "")},
        )

    parents = set(config.get("known_threads", []))
    params = dict(channel=channel, oldest=f"{start:.6f}", latest=f"{end:.6f}", inclusive="true", limit=100)
    for msg in _slack_pages("conversations.history", token, params):
        ts = msg["ts"]
        if float(ts) >= start:
            yield event(msg, msg.get("thread_ts", ts))
        if msg.get("reply_count") and float(msg.get("latest_reply") or end) >= start:
            parents.add(ts)
    for ts in sorted(parents):
        if not re.fullmatch(r"\d+\.\d+", ts) or float(ts) > end:
            continue
        try:
            for reply in _slack_pages("conversations.replies", token, dict(params, ts=ts)):
                if reply["ts"] != ts:
                    yield event(reply, ts)
        except Rejected as error:
            if str(error) != "Slack returned thread_not_found.":
                raise


def _gh(path, token, params=None, body=None):
    return _http("https://api.github.com" + path, token, params, body)


def _gh_pages(path, token, since, end):
    page = 1
    while True:
        rows, headers = _gh(
            path,
            token,
            dict(
                since=since,
                per_page=100,
                page=page,
                sort="updated",
                direction="asc",
                **({"state": "all"} if path.endswith("/issues") else {}),
            ),
        )
        for row in rows:
            if _when(row["updated_at"]) > end:
                return
            yield row
        if 'rel="next"' not in headers.get("Link", ""):
            return
        page += 1


def _iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _when(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _github_event(repo, user, item, issue, key, title, body):
    number = issue["number"]
    others = {a["login"].casefold() for a in issue.get("assignees", [])}
    return dict(
        key=key,
        timestamp=_when(item["updated_at"]),
        title=title,
        body=body,
        authored=(item.get("user") or {}).get("login", "").casefold() == user.casefold(),
        owned=bool(others and user.casefold() not in others),
        account=user,
        destination=f"{repo}#{number}",
        url=item["html_url"],
        context={
            "repository": repo,
            "number": number,
            "author": (item.get("user") or {}).get("login", ""),
            "state": issue["state"],
            "assignees": sorted(others),
            "is_pull_request": "pull_request" in issue,
        },
    )


def _github_issue(repo, user, issue, token):
    number = issue["number"]
    body = issue.get("body") or ""
    requested = False
    if "pull_request" in issue:
        pr = _gh(f"/repos/{repo}/pulls/{number}", token)[0]
        requested = any(r["login"].casefold() == user.casefold() for r in pr.get("requested_reviewers", []))
    row = _github_event(repo, user, issue, issue, f"issue:{repo}:{number}", issue["title"], body)
    if requested:
        row.update(owned=False, title="Review requested: " + row["title"])
        row["context"]["review_requested"] = True
    return row


def github_open_work(account, view, page):
    qualifiers = {
        "assigned": "is:issue assignee:",
        "issues": "is:issue author:",
        "pulls": "is:pr author:",
        "reviews": "is:pr review-requested:",
    }
    if view not in qualifiers or type(page) is not int or not 1 <= page <= 40:
        raise ValueError("Choose a valid GitHub work page.")
    token = github_credential(account)
    query = "is:open " + qualifiers[view] + account["identity"]
    response, _ = _gh(
        "/search/issues", token, dict(q=query, sort="updated", order="desc", per_page=25, page=page)
    )
    if response.get("incomplete_results"):
        raise ValueError("GitHub returned incomplete search results. Try again.")
    items = []
    for row in response["items"]:
        repo = validate(
            "github", {"repository": row["repository_url"].removeprefix("https://api.github.com/repos/")}
        )["repository"]
        number = row["number"]
        if type(number) is not int or number < 1:
            raise ValueError("GitHub returned an invalid issue number.")
        items.append(dict(repo=repo, number=number, title=row["title"], url=row["html_url"]))
    return dict(
        items=items, total=response["total_count"], more=page * 25 < min(response["total_count"], 1000)
    )


def github_selected_issue(source, number):
    if type(number) is not int or number < 1:
        raise ValueError("Choose an open GitHub item.")
    token = github_credential(source)
    user = _gh("/user", token)[0]["login"]
    repo = validate("github", source["config"])["repository"]
    if source["identity"].casefold() != user.casefold():
        raise ValueError("Reconnect the original GitHub account.")
    row = _gh(f"/repos/{repo}/issues/{number}", token)[0]
    if row["number"] != number or row["state"] != "open":
        raise ValueError("This item is no longer open.")
    return _github_issue(repo, user, row, token)


def _gh_events(config, token, start, end):
    user = _gh("/user", token)[0]["login"]
    if config.get("identity") and config["identity"].casefold() != user.casefold():
        raise Rejected("The connected GitHub account changed.")
    repo = config["repository"]
    path = "/repos/" + repo
    since = _iso(start - 1)
    issues = {}

    for issue in _gh_pages(path + "/issues", token, since, end):
        number = issue["number"]
        issues[number] = issue
        if not start <= _when(issue["updated_at"]) <= end:
            continue
        yield _github_issue(repo, user, issue, token)
    for comment in _gh_pages(path + "/issues/comments", token, since, end):
        if not start <= _when(comment["updated_at"]) <= end:
            continue
        number = int(comment["issue_url"].rsplit("/", 1)[-1])
        if number not in issues:
            issues[number] = _gh(path + f"/issues/{number}", token)[0]
        issue = issues[number]
        row = _github_event(
            repo,
            user,
            comment,
            issue,
            f"comment:{repo}:{comment['id']}",
            issue["title"],
            comment.get("body") or "",
        )
        # A commenter can request a distinct review on a bug assigned to someone else.
        # Retain issue ownership as context for the discovery and coding workers.
        row["owned"] = False
        yield row


def _imap_ok(result, operation):
    status, rows = result
    if status != "OK":
        raise RuntimeError("IMAP failed to " + operation + ".")
    return rows


def _imap_date(timestamp):
    value = dt.datetime.fromtimestamp(timestamp, dt.timezone.utc)
    month = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[value.month - 1]
    return f"{value.day:02}-{month}-{value.year}"


def _imap_time(metadata):
    match = re.search(rb'INTERNALDATE " *(\d{1,2})-(\w{3})-(\d{4}) (\d\d:\d\d:\d\d [+-]\d{4})"', metadata)
    if not match:
        raise RuntimeError("IMAP omitted the message arrival date.")
    return parsedate_to_datetime(b" ".join(match.groups()).decode("ascii")).timestamp()


def verify_gmail_password(address, password):
    try:
        with imaplib.IMAP4_SSL(
            "imap.gmail.com", 993, ssl_context=ssl.create_default_context(), timeout=20
        ) as client:
            _imap_ok(client.login(address, password), "sign in")
    except (imaplib.IMAP4.error, RuntimeError, OSError):
        raise ValueError(
            "Could not sign in to Gmail. Check the address and app password, or use Google browser sign-in."
        ) from None


def _mail_events(config, secret, start, end):
    address = config["address"]
    tls = ssl.create_default_context()
    with imaplib.IMAP4_SSL(
        config["imap_host"], int(config["imap_port"]), ssl_context=tls, timeout=30
    ) as client:
        sent_mailbox = config["sent_mailbox"]
        if config.get("xoauth2"):
            sasl = f"user={address}\x01auth=Bearer {secret}\x01\x01".encode()
            _imap_ok(client.authenticate("XOAUTH2", lambda challenge: b"" if challenge else sasl), "sign in")
        else:
            _imap_ok(client.login(address, secret), "sign in")
        if config.get("xoauth2") or config.get("gmail_password"):
            if not sent_mailbox:
                folders = _imap_ok(client.list(), "list mailboxes")
                for folder in folders:
                    if (
                        isinstance(folder, bytes)
                        and (match := re.fullmatch(rb'\(([^)]*)\) "[^"]*" (.+)', folder))
                        and b"\\sent" in match[1].lower().split()
                    ):
                        sent_mailbox = match[2].decode("ascii").strip('"')
                        break
        for box in dict.fromkeys(filter(None, [config["mailbox"], sent_mailbox])):
            # IMAP SINCE/BEFORE compare local calendar dates including server offset.
            name = '"' + box.replace("\\", "\\\\").replace('"', '\\"') + '"'
            _imap_ok(client.select(name, readonly=True), "open mailbox")
            validity = client.response("UIDVALIDITY")[1][0]
            if not validity:
                raise RuntimeError("IMAP omitted mailbox UIDVALIDITY.")
            ids = _imap_ok(
                client.uid(
                    "SEARCH", None, "SINCE", _imap_date(start - 86400), "BEFORE", _imap_date(end + 172800)
                ),
                "search mailbox",
            )
            for uid in b" ".join(ids).split():
                metadata = _imap_ok(client.uid("FETCH", uid, "(INTERNALDATE)"), "read message date")
                timestamp = _imap_time(next((row for row in metadata if isinstance(row, bytes)), b""))
                if not start <= timestamp <= end:
                    continue
                rows = _imap_ok(client.uid("FETCH", uid, "(BODY.PEEK[])"), "read message")
                fetched = next((row for row in rows if isinstance(row, tuple)), None)
                if not fetched:
                    raise RuntimeError("IMAP could not retrieve a message. Retry the scan.")
                msg = BytesParser(policy=policy.default).parsebytes(fetched[1])
                authored = (
                    box == sent_mailbox
                    and parseaddr(str(msg.get("From", "")))[1].casefold() == address.casefold()
                )
                target = msg.get("To") if authored else msg.get("Reply-To") or msg.get("From")
                destination = parseaddr(str(target or ""))[1]
                part = msg.get_body(preferencelist=("plain", "html"))
                body = part.get_content() if part is not None else ""
                subject = " ".join(str(msg.get("Subject") or "(no subject)").splitlines())
                message_id = str(msg.get("Message-ID") or "").strip()
                ref = message_id if re.fullmatch(r"<[^\s<>]+>", message_id) else ""
                history = re.findall(
                    r"<[^\s<>]+>", str(msg.get("References") or msg.get("In-Reply-To") or "")
                )
                references = list(dict.fromkeys(history + ([ref] if ref else [])))
                key = f"{box}:{validity.decode()}:{uid.decode()}"
                yield dict(
                    key=key,
                    timestamp=timestamp,
                    title=subject,
                    body=str(body),
                    owned=False,
                    authored=authored,
                    account=address,
                    destination=destination,
                    url="mid:" + quote(ref[1:-1], safe="@") if ref else "",
                    context={
                        "recipient": destination,
                        "subject": subject,
                        "message_id": ref,
                        "references": references,
                        "thread": references[0] if references else "",
                        "author": parseaddr(str(msg.get("From", "")))[1],
                        "mailbox": box,
                        "uid": uid.decode(),
                    },
                )


def poll(conn, start, end):
    """Read one complete inclusive window or raise so callers retain their checkpoint."""
    config = validate(conn["kind"], conn["config"])
    if conn["kind"] == "slack":
        config["known_threads"] = conn.get("known_threads", [])
    if conn.get("github_parent") or conn.get("auth_method") == "gh_repository":
        config["identity"] = conn["identity"]
    secret = _transport_secret(conn, config)
    source = {"email": _mail_events, "slack": _slack_events, "github": _gh_events}[conn["kind"]]
    rows = {row["key"]: row for row in source(config, secret, start, end) if start <= row["timestamp"] <= end}
    return sorted(rows.values(), key=lambda row: (row["timestamp"], row["key"]))


def coverage_gap(conn):
    if conn["kind"] == "slack":
        return "Slack history omits replies to unknown threads started before the scan window."
    return ""


def authored_receipt(raw):
    """Match an observed outgoing message to the executor receipt for this connection."""
    if not raw.get("authored"):
        return ""
    ctx = raw.get("context", {})
    if ctx.get("gmail_id"):
        return "Gmail sent " + ctx["gmail_id"]
    if ctx.get("message_id"):
        return "SMTP accepted " + ctx["message_id"]
    if ctx.get("channel"):
        return f"Slack {ctx['channel']} / {raw['key'].rsplit('/', 1)[-1]}"
    if raw.get("key", "").startswith(("comment:", "issue:")):
        return raw.get("url", "")
    return ""


def _mail_send(config, secret, event, text):
    context = event["context"]
    recipient = context["recipient"]
    if (
        config["address"] != event["account"]
        or recipient != event["destination"]
        or not _ADDRESS.fullmatch(recipient)
    ):
        raise Rejected("Email account or recipient does not match the reviewed action.")
    msg = EmailMessage()
    msg["From"], msg["To"] = config["address"], recipient
    subject = context["subject"]
    msg["Subject"] = subject if subject.casefold().startswith("re:") else "Re: " + subject
    if context["message_id"]:
        msg["In-Reply-To"] = context["message_id"]
        msg["References"] = " ".join(context.get("references") or [context["message_id"]])
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=config["address"].rsplit("@", 1)[1])
    msg.set_content(text)
    host, port = config["smtp_host"], int(config["smtp_port"])
    tls = ssl.create_default_context()
    smtp = None
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, context=tls, timeout=30)
        else:
            smtp = smtplib.SMTP(host, port, timeout=30)
            smtp.starttls(context=tls)
        if config.get("xoauth2"):
            sasl = "user=" + config["address"] + "\x01auth=Bearer " + secret + "\x01\x01"
            smtp.ehlo_or_helo_if_needed()
            smtp.auth("XOAUTH2", lambda challenge=None: sasl if challenge is None else "")
        else:
            smtp.login(config["address"], secret)
    except Exception:
        if smtp is not None:
            smtp.close()
        raise Rejected("Could not establish an authenticated SMTP connection.") from None
    try:
        refused = smtp.send_message(msg, from_addr=config["address"], to_addrs=[recipient])
        if refused:
            raise Rejected("SMTP rejected the recipient.")
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPResponseException) as error:
        raise Rejected(f"SMTP rejected delivery ({getattr(error, 'smtp_code', 'recipient')}).") from None
    finally:
        # QUIT failures do not change acceptance of the preceding DATA command.
        smtp.close()
    return "SMTP accepted " + str(msg["Message-ID"])


def github_identity(conn):
    if conn["kind"] != "github":
        raise Rejected("Choose a GitHub connection.")
    return _gh("/user", github_credential(conn))[0]["login"]


def github_request(conn, method, suffix, data=None):
    """Executor-only API rooted in the configured repository. GET data is a query dict."""
    if conn["kind"] != "github" or method not in ("GET", "POST", "PATCH", "PUT"):
        raise Rejected("Unsupported GitHub operation.")
    # Branch names containing slash may be supplied as separate path segments.
    segments = [] if suffix == "" and method == "GET" else suffix.removeprefix("/").split("/")
    if any(not re.fullmatch(r"[A-Za-z0-9_.@~-]+", s) or s in (".", "..") for s in segments):
        raise Rejected("Invalid repository API path.")
    repo = validate("github", conn["config"])["repository"]
    path = "/".join([repo, *segments])
    result, _ = _http(
        "https://api.github.com/repos/" + path,
        github_credential(conn),
        params=data if method == "GET" else None,
        body=data if method != "GET" else None,
        method=method,
    )
    return result


def send(conn, event, text):
    """Submit once. Rejected means no delivery; any other failure may be ambiguous."""
    kind = conn["kind"]
    try:
        config = validate(kind, conn["config"])
        secret = _transport_secret(conn, config)
        ctx = event["context"]
        if ctx.get("google_service") or (
            conn.get("google_services") and "Gmail" not in conn["google_services"]
        ):
            raise Rejected("This Google source is not an outgoing mailbox.")
        if kind == "slack":
            identity = _slack_identity(secret)
            channel, thread = ctx["channel"], ctx["thread"]
            if (
                channel != config["channel"]
                or not re.fullmatch(r"\d+\.\d+", thread)
                or event["destination"] != channel + " / thread " + thread
            ):
                raise Rejected("Slack reply target changed.")
        elif kind == "github":
            identity = _gh("/user", secret)[0]["login"]
            repo, number = config["repository"], ctx["number"]
            if (
                ctx["repository"] != repo
                or not isinstance(number, int)
                or number <= 0
                or event["destination"] != f"{repo}#{number}"
            ):
                raise Rejected("GitHub reply target changed.")
        else:
            identity = config["address"]
        if identity != event["account"]:
            raise Rejected("The connected account differs from the reviewed sender.")
        if not isinstance(text, str) or not text.strip():
            raise Rejected("The reviewed message is empty.")
        if kind == "slack" and len(text) > 40000:
            raise Rejected("The message exceeds Slack’s maximum length.")
    except Rejected:
        raise
    except Exception:
        raise Rejected("Could not verify the sending account and destination.") from None
    if kind == "slack":
        result = _slack(
            "chat.postMessage",
            secret,
            body=dict(channel=channel, thread_ts=thread, text=text, unfurl_links=False, unfurl_media=False),
        )
        return f"Slack {result['channel']} / {result['ts']}"
    if kind == "github":
        result, _ = _gh(f"/repos/{repo}/issues/{number}/comments", secret, body={"body": text})
        return result["html_url"]
    return _mail_send(config, secret, event, text)
