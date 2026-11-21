"""Typed adapter for the bundled Google Workspace extension's stdio MCP interface."""

import base64
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.parse import quote

import connectors
from google_workspace import utc

ARCHIVE = Path(__file__).parent / "vendor/google-workspace/darwin-v0.0.8.tar.gz"
SHA256 = "ca53101fd8b355d7710ffc28d55b4f558df621861ff8cdb9e5e426987f290e80"
_LOCKS = [threading.Lock() for _ in range(64)]


def profile_path(root, ident):
    if not re.fullmatch(r"[a-f0-9]{32}", ident):
        raise ValueError("Invalid Google connection.")
    return Path(root).resolve() / "google-accounts" / ident


def make_profile(root, ident):
    folder = profile_path(root, ident)
    if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != SHA256:
        raise ValueError("The bundled Google connector failed its integrity check.")
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with tarfile.open(ARCHIVE, "r:gz") as archive:
            for name in ("dist/index.js", "gemini-extension.json"):
                target = folder / name
                target.parent.mkdir(mode=0o700, exist_ok=True)
                with archive.extractfile(name) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
        connectors.store_secret("workspace-key:" + ident, secrets.token_hex(32))
    except BaseException:
        shutil.rmtree(folder)
        raise


def desktop_entry(folder):
    # Keep the upstream distribution pinned. Install the same adapter for new and existing profiles.
    entry = folder / "dist/memyselfai-v1.cjs"
    if entry.is_file():
        return entry
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != SHA256:
            raise ValueError("The bundled Google connector failed its integrity check.")
        script = archive.extractfile("dist/index.js").read().decode()
    anchor = 'u("people.getMe",'
    prompt = 'scope:this.scopes,state:P,prompt:"consent"'
    if script.count(anchor) != 1 or script.count(prompt) != 1:
        raise ValueError("The Google connector does not match its desktop adapter.")
    handler = (ARCHIVE.parent / "account-email.cjs").read_text()
    # Evaluate the small CommonJS module in its own scope inside the upstream entry.
    register = (
        'u("auth.accountEmail",{description:"Read the signed-in account email",inputSchema:{},...h},'
        "()=>{const module={};" + handler + "\nreturn module.exports(U)}),"
    )
    script = script.replace(prompt, prompt.replace('"consent"', '"select_account consent"')).replace(
        anchor, register + anchor
    )
    with tempfile.NamedTemporaryFile(dir=entry.parent, delete=False) as out:
        out.write(script.encode())
    os.replace(out.name, entry)
    return entry


def forget(root, ident):
    path = profile_path(root, ident)
    with _LOCKS[hash(str(path)) % len(_LOCKS)]:
        if path.exists():
            connectors.delete_secret("workspace-key:" + ident)
            shutil.rmtree(path)


class Client:
    def __init__(self, proc, cancel):
        self.proc = proc
        self.cancel = cancel
        self.pending = b""
        self.seq = 0
        self.selector = selectors.DefaultSelector()
        self.selector.register(proc.stdout, selectors.EVENT_READ)

    def write(self, msg):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}).encode() + b"\n")
        self.proc.stdin.flush()

    def request(self, method, params, timeout=45):
        self.seq += 1
        self.write(dict(id=self.seq, method=method, params=params))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.cancel and self.cancel.is_set():
                raise ValueError("Google sign-in cancelled.")
            if b"\n" not in self.pending:
                if not self.selector.select(0.25):
                    continue
                data = os.read(self.proc.stdout.fileno(), 65536)
                if not data:
                    raise ValueError("The Google connector stopped. Reopen the connection and try again.")
                self.pending += data
                if len(self.pending) > 32 * 1024 * 1024:
                    raise ValueError("The Google response was too large.")
                continue
            line, self.pending = self.pending.split(b"\n", 1)
            msg = json.loads(line)
            if msg.get("id") != self.seq or "method" in msg:
                continue
            if "error" in msg:
                raise ValueError("The Google connector declined the request.")
            return msg["result"]
        raise ValueError("Google did not respond in time. Try connecting again.")

    def call(self, name, args, timeout=45):
        r = self.request("tools/call", dict(name=name, arguments=args), timeout)
        items = r.get("content") or []
        if r.get("isError") or not items or items[0].get("type") != "text":
            raise ValueError("Google could not complete " + name + ". Check the connection and permissions.")
        text = items[0]["text"]
        try:
            result = json.loads(text)
        except ValueError:
            result = text
        if isinstance(result, dict) and result.get("error"):
            raise ValueError("Google could not complete " + name + ". Check the connection and permissions.")
        return result


@contextmanager
def session(root, ident, cancel=None):
    folder = profile_path(root, ident)
    bundled = Path(__file__).resolve().parent.parent / "Helpers/workspace-node"
    node = (
        str(bundled)
        if bundled.is_file()
        else shutil.which("node")
        or next(
            (str(p) for p in (Path("/opt/homebrew/bin/node"), Path("/usr/local/bin/node")) if p.is_file()),
            None,
        )
    )
    if not node:
        raise ValueError("The Google Workspace connector needs Node.js 20 or later on this Mac.")
    with _LOCKS[hash(str(folder)) % len(_LOCKS)]:
        if not (folder / "dist/index.js").is_file():
            raise ValueError("Reconnect the Google account to restore its profile.")
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "WORKSPACE_CLIENT_ID",
                "WORKSPACE_CLOUD_FUNCTION_URL",
                "WORKSPACE_FEATURE_OVERRIDES",
                "NODE_OPTIONS",
                "NODE_PATH",
                "BROWSER",
                "OAUTH_CALLBACK_HOST",
                "DEBUG",
                "SSH_CONNECTION",
                "DEBIAN_FRONTEND",
            }
        }
        env["GEMINI_CLI_WORKSPACE_FORCE_FILE_STORAGE"] = "true"
        if cancel is not None:
            env.pop("CI", None)
        else:
            env["CI"] = "1"
            if not (folder / "gemini-cli-workspace-token.json").is_file():
                raise ValueError("Reconnect this Google account.")
        master = folder / ".gemini-cli-workspace-master-key"
        key = bytes.fromhex(connectors._secret("workspace-key:" + ident))
        if len(key) != 32:
            raise ValueError("Reconnect this Google account to restore its encryption key.")
        try:
            with os.fdopen(
                os.open(master, os.O_WRONLY | os.O_TRUNC | os.O_CREAT | os.O_NOFOLLOW, 0o600), "wb"
            ) as out:
                out.write(key)
            proc = subprocess.Popen(
                [node, str(desktop_entry(folder))],
                cwd=folder,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            client = Client(proc, cancel)
            try:
                client.request(
                    "initialize",
                    dict(
                        protocolVersion="2024-11-05",
                        clientInfo=dict(name="MeMyselfAI", version="1"),
                        capabilities={},
                    ),
                    15,
                )
                client.write(dict(method="notifications/initialized"))
                yield client
            finally:
                client.selector.close()
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                proc.stdin.close()
                proc.stdout.close()
        finally:
            master.unlink(missing_ok=True)


def address(client, timeout=45):
    result = client.call("auth_accountEmail", {}, timeout)
    email = result.get("email") if isinstance(result, dict) else None
    if isinstance(email, str) and connectors._ADDRESS.fullmatch(email):
        return email
    raise ValueError(
        "Google did not share an account email. Connect again and allow access to Drive, Gmail or Calendar."
    )


def accessible_services(client):
    # Probe each service without fetching message bodies or document contents.
    probes = {
        "Gmail": ("gmail_search", {"query": "after:4102444800", "maxResults": 1}, dict),
        "Calendar": (
            "calendar_listEvents",
            {"calendarId": "primary", "timeMin": "2100-01-01T00:00:00Z", "timeMax": "2100-01-01T00:00:01Z"},
            list,
        ),
        "Drive": (
            "drive_search",
            {"query": "trashed = false and modifiedTime > '2100-01-01T00:00:00Z'", "pageSize": 1},
            dict,
        ),
    }
    granted = []
    for service, (tool, args, expected) in probes.items():
        if client.cancel and client.cancel.is_set():
            raise ValueError("Google sign-in cancelled.")
        try:
            if isinstance(client.call(tool, args), expected):
                granted.append(service)
        except ValueError:
            continue
    if not granted:
        raise ValueError(
            "Google access could not be verified for Gmail, Calendar or Drive. Check the permissions and try again."
        )
    return granted


def verify(client, conn):
    if address(client).casefold() != conn["identity"].casefold():
        raise connectors.Rejected("The Google account differs from the reviewed account.")


def pages(client, tool, args, key):
    seen = set()
    while True:
        result = client.call(tool, args)
        yield from result.get(key, [])
        cursor = result.get("nextPageToken")
        if not cursor:
            return
        if cursor in seen:
            raise ValueError("Google repeated a page token.")
        args = dict(args, pageToken=cursor)
        seen.add(cursor)


def messages(client, email, start, end):
    hits = pages(
        client,
        "gmail_search",
        dict(query=f"{{in:inbox in:sent}} after:{int(start) - 1} before:{int(end) + 1}", maxResults=100),
        "messages",
    )
    for hit in hits:
        item = client.call("gmail_get", dict(messageId=hit["id"], format="raw"))
        stamp = int(item["internalDate"]) / 1000
        if not start <= stamp <= end:
            continue
        raw = item["raw"]
        msg = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        )
        sender = parseaddr(str(msg.get("From", "")))[1]
        own = "SENT" in item.get("labelIds", []) and sender.casefold() == email.casefold()
        target = parseaddr(str((msg.get("To") if own else msg.get("Reply-To") or msg.get("From")) or ""))[1]
        subject = " ".join(str(msg.get("Subject") or "(no subject)").splitlines())
        part = msg.get_body(preferencelist=("plain", "html"))
        key = item["id"]
        thread = item["threadId"]
        yield dict(
            key="gmail/" + key,
            timestamp=stamp,
            title=subject,
            body=str(part.get_content())[:20000] if part else "",
            account=email,
            authored=own,
            owned=False,
            destination=target,
            url="https://mail.google.com/mail/u/?authuser="
            + quote(email, safe="")
            + "#all/"
            + quote(thread, safe=""),
            context=dict(
                recipient=target,
                author=sender,
                subject=subject,
                thread=thread,
                gmail_id=key,
                message_id=str(msg.get("Message-ID", "")),
            ),
        )


def resources(client, service, email, start, end):
    if service == "Drive":
        rows = pages(
            client,
            "drive_search",
            dict(
                query=f"trashed = false and modifiedTime >= '{utc(start)}' and modifiedTime <= '{utc(end)}'",
                pageSize=100,
            ),
            "files",
        )
    elif service == "Calendar":
        rows = client.call(
            "calendar_listEvents", dict(calendarId="primary", timeMin=utc(start), timeMax=utc(end))
        )
        if not isinstance(rows, list):
            raise ValueError("Google returned an incomplete calendar listing.")
    else:
        raise ValueError("Unsupported Workspace connector service.")
    for r in rows:
        if service == "Drive":
            stamp = datetime.fromisoformat(r["modifiedTime"].replace("Z", "+00:00")).timestamp()
        else:
            if re.fullmatch(r"MeMyselfAI task [a-f0-9]{64}", r.get("description") or ""):
                continue
            dt = r["start"].get("dateTime") or r["start"].get("date")
            stamp = datetime.fromisoformat(dt.replace("Z", "+00:00")).timestamp()
        if not start <= stamp <= end:
            continue
        title = r.get("name") or r.get("summary") or "Untitled"
        yield dict(
            key="workspace/" + service + "//" + r["id"],
            timestamp=stamp,
            title=title,
            body=r.get("description") or r.get("mimeType") or title,
            account=email,
            authored=False,
            owned=False,
            destination=service + " · " + title,
            url=r.get("htmlLink", ""),
            context=dict(google_service=service, resource=r["id"], mime_type=r.get("mimeType", "")),
        )


def poll(root, conn, service, start, end):
    with session(root, conn["workspace_profile"]) as client:
        verify(client, conn)
        return list(
            messages(client, conn["identity"], start, end)
            if service == "Gmail"
            else resources(client, service, conn["identity"], start, end)
        )


def excerpt(root, conn, ctx):
    tools = {
        "application/vnd.google-apps.document": ("docs_getText", "documentId"),
        "application/vnd.google-apps.spreadsheet": ("sheets_getText", "spreadsheetId"),
        "application/vnd.google-apps.presentation": ("slides_getText", "presentationId"),
    }
    tool = tools.get(ctx.get("mime_type"))
    if not tool:
        return ""
    with session(root, conn["workspace_profile"]) as client:
        verify(client, conn)
        result = client.call(tool[0], {tool[1]: ctx["resource"]})
        return (result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))[:16000]


def reply(root, conn, event, text):
    ctx = event["context"]
    if (
        event["account"] != conn["identity"]
        or not ctx.get("gmail_id")
        or not ctx.get("thread")
        or not connectors._ADDRESS.fullmatch(event["destination"])
        or ctx.get("recipient") != event["destination"]
    ):
        raise connectors.Rejected("The Gmail account or reply destination changed.")
    subject = ctx["subject"]
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    with session(root, conn["workspace_profile"]) as client:
        verify(client, conn)
        # Both provider writes run inside the executor's version-specific approval.
        draft = client.call(
            "gmail_createDraft",
            dict(to=event["destination"], subject=subject, body=text, threadId=ctx["thread"]),
        )
        if not draft.get("id") or draft.get("message", {}).get("threadId") != ctx["thread"]:
            raise RuntimeError("Gmail did not confirm the reviewed draft thread.")
        sent = client.call("gmail_sendDraft", dict(draftId=draft["id"]))
        if not sent.get("id") or sent.get("threadId") != ctx["thread"]:
            raise RuntimeError("Gmail did not confirm the delivery receipt.")
        return "Gmail sent " + sent["id"]


def calendar_payload(body):
    return dict(
        calendarId="primary",
        summary=body["summary"],
        description="MeMyselfAI task " + body["id"],
        start=body["start"],
        end=body["end"],
        sendUpdates="none",
    )


def create_event(root, conn, task):
    body = json.loads(task["draft"])
    if (
        body != task["calendar_payload"]
        or task["account"] != conn["identity"]
        or task["destination"] != "Primary calendar"
        or "Calendar" not in conn.get("google_services", [])
        or body.get("calendarId") != "primary"
        or body.get("sendUpdates") != "none"
    ):
        raise connectors.Rejected("The calendar preview changed. Prepare a new preview.")
    with session(root, conn["workspace_profile"]) as client:
        verify(client, conn)
        events = client.call(
            "calendar_listEvents",
            dict(calendarId="primary", timeMin=body["start"]["dateTime"], timeMax=body["end"]["dateTime"]),
        )
        if not isinstance(events, list):
            raise connectors.Rejected("Could not check the reviewed calendar time slot.")
        for existing in events:
            if existing.get("description") != body["description"]:
                continue
            if existing.get("summary") != body["summary"] or any(
                not existing.get(field, {}).get("dateTime")
                or datetime.fromisoformat(existing[field]["dateTime"].replace("Z", "+00:00"))
                != datetime.fromisoformat(body[field]["dateTime"].replace("Z", "+00:00"))
                for field in ("start", "end")
            ):
                raise connectors.Rejected(
                    "An event with this task reference already exists with different details."
                )
            return existing.get("htmlLink") or "Calendar event " + existing["id"]
        result = client.call("calendar_createEvent", body)
        if not result.get("id"):
            raise RuntimeError("Google did not confirm creation. Check the calendar before retrying.")
        return result.get("htmlLink") or "Calendar event " + result["id"]
