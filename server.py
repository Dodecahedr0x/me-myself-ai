"""Authenticated loopback transport for the native desktop window."""

import argparse
import fcntl
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlsplit

import worker
from engine import Engine
from connectors import SCHEMAS

UI = Path(__file__).resolve().parent / "ui"
DEFAULT_DATA = Path.home() / "Library/Application Support/MeMyselfAI"


_folder_picker_lock = threading.Lock()


def choose_repository():
    """Show the system folder dialog when the dashboard runs in a regular browser."""
    if sys.platform != "darwin":
        raise ValueError("The system folder picker requires macOS.")
    if not _folder_picker_lock.acquire(blocking=False):
        raise ValueError("A folder picker is already open.")
    script = '''activate
try
    return POSIX path of (choose folder with prompt "Choose a folder")
on error number -128
    return ""
end try'''
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script], capture_output=True, timeout=600, check=True
        )
        path = result.stdout.removesuffix(b"\n").decode("utf-8")
        return {"path": os.path.normpath(path) if path else ""}
    except (subprocess.SubprocessError, OSError, UnicodeError):
        raise ValueError("Could not open the folder picker. Try again or enter a repository path.") from None
    finally:
        _folder_picker_lock.release()


class HTTP(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, engine, port=0):
        super().__init__(("127.0.0.1", port), Handler)
        self.engine = engine
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.ticket = secrets.token_urlsafe(32)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.launch_url = self.origin + "/launch/" + self.ticket


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # URLs can contain a launch ticket.

    def output(self, status, body=b"", ctype="application/json", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        for key, val in extra:
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(body)

    def session_ok(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            value = cookie["memyself_session"].value
        except Exception:
            return False
        return secrets.compare_digest(value, self.server.session)

    def valid_host(self):
        return self.headers.get("Host") == urlsplit(self.server.origin).netloc

    def do_GET(self):
        s = self.server
        path = urlsplit(self.path).path
        if not self.valid_host():
            return self.output(403)
        if s.ticket and secrets.compare_digest(path, "/launch/" + s.ticket):
            s.ticket = ""
            return self.output(
                303,
                extra=[
                    ("Location", "/"),
                    ("Set-Cookie", "memyself_session=" + s.session + "; HttpOnly; SameSite=Strict; Path=/"),
                ],
            )
        if not self.session_ok():
            return self.output(403)
        if path.startswith("/api/"):
            if path == "/api/providers":
                return self.output(200, json.dumps(worker.cli_inventory()).encode())
            if path not in {"/api/state", "/api/connectors"}:
                return self.output(404)
            data = s.engine.state() if path == "/api/state" else SCHEMAS
            return self.output(200, json.dumps(data, ensure_ascii=False).encode())
        name = path.lstrip("/") or "index.html"
        if "/" in name or name.startswith(".") or not (UI / name).is_file():
            return self.output(404)
        data = (UI / name).read_bytes()
        if name == "index.html":
            data = data.replace(b"__APP_TOKEN__", s.csrf.encode())
        return self.output(200, data, mimetypes.guess_type(name)[0] or "application/octet-stream")

    def do_POST(self):
        s = self.server
        if (
            not self.valid_host()
            or not self.session_ok()
            or self.path != "/api/action"
            or self.headers.get("Origin", s.origin) != s.origin
            or not secrets.compare_digest(self.headers.get("X-App-Token", ""), s.csrf)
        ):
            return self.output(403)
        try:
            size = int(self.headers.get("Content-Length", 0))
            if not 0 < size <= 1_000_000:
                raise ValueError("Invalid request size.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected an action object.")
            if data.get("op") == "choose_repository":
                result = choose_repository()
            elif data.get("op") == "open_folder_settings":
                if sys.platform != "darwin":
                    raise ValueError("Folder privacy settings are available on macOS.")
                try:
                    subprocess.run(["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_FilesAndFolders"],
                                   check=True, timeout=10, capture_output=True)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError("Open Privacy & Security in System Settings, then choose Files & Folders.") from exc
                result = None
            else:
                result = s.engine.action(data)
            self.output(200, json.dumps({"ok": True, **(result or {})}).encode())
        except (ValueError, RuntimeError, OSError, KeyError) as exc:
            self.output(400, json.dumps({"error": str(exc)}).encode())
        except Exception:
            self.output(500, b'{"error":"The action failed. Inspect the activity history."}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    os.umask(0o077)
    args.data.mkdir(parents=True, exist_ok=True)
    with (args.data / "app.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("This workspace is already open.")
        engine = Engine(args.data)
        server = HTTP(engine, args.port)
        engine.start()
        print(server.launch_url, flush=True)
        try:
            server.serve_forever()
        finally:
            engine.close()
            server.server_close()


if __name__ == "__main__":
    main()
