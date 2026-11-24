"""Install, start and pair a VPS instance over one of the user's SSH hosts.

See docs/specs/guided-setup.md, "VPS setup over SSH". Every remote command goes through one
multiplexed OpenSSH connection in batch mode, so the user's own config, keys and agent apply and no
prompt can hide in the background. A sudo password, when needed, is held in memory for one run and
reaches sudo only on standard input.
"""

import glob
import http.client
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from urllib.parse import urlsplit

SYNC_PORT = 8802
SERVICE_USER = "memyselfai"
REMOTE_DIR = ".memyselfai-setup"
LAUNCH_FILE = "/var/lib/memyselfai/workspace/launch-url"
PACKAGES = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex"}
SIGN_IN = {"claude": "claude auth login", "codex": "codex login --device-auth"}
STEPS = (
    ("connect", "Connect over SSH"),
    ("copy", "Copy the app"),
    ("install", "Install the service"),
    ("agent", "Install the task agent"),
    ("wait", "Start the service"),
    ("pair", "Pair with this Mac"),
)
# A host alias or user@host. Never an option: OpenSSH would read a leading "-" as a flag.
HOST_RE = re.compile(r"[A-Za-z0-9_.@%+\[\]:][A-Za-z0-9_.@%+\[\]:-]{0,252}")


class SetupError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def _config_files(path, home, seen):
    path = Path(path)
    if path in seen or not path.is_file():
        return
    seen.add(path)
    yield path
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "include":
            for pattern in shlex.split(parts[1]):
                pattern = os.path.expanduser(pattern)
                if not os.path.isabs(pattern):
                    pattern = str(Path(home) / ".ssh" / pattern)
                for match in sorted(glob.glob(pattern)):
                    yield from _config_files(match, home, seen)


def ssh_hosts(home=None):
    """Concrete Host aliases from ~/.ssh/config and the files it includes, in file order."""
    home = Path(home or Path.home())
    hosts = []
    for path in _config_files(home / ".ssh" / "config", home, set()):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or parts[0].lower() != "host":
                continue
            for name in shlex.split(parts[1]):
                if not any(ch in name for ch in "*?!") and HOST_RE.fullmatch(name) and name not in hosts:
                    hosts.append(name)
    return hosts


def valid_host(host):
    host = str(host or "").strip()
    if not HOST_RE.fullmatch(host):
        raise ValueError("Choose an SSH host from your configuration, or enter user@host.")
    return host


def resolve(host, ssh="ssh"):
    """What OpenSSH would connect to for this destination, read with ssh -G (no connection)."""
    try:
        done = subprocess.run([ssh, "-G", valid_host(host)], capture_output=True, timeout=10,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        raise SetupError("Could not run ssh. Install OpenSSH and try again.") from None
    if done.returncode:
        raise SetupError(_tail(done.stderr) or "ssh could not read the configuration for this host.")
    values = {}
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        key, _, value = line.partition(" ")
        values.setdefault(key.lower(), value.strip())
    return dict(hostname=values.get("hostname") or host, user=values.get("user", ""),
                port=values.get("port", "22"))


def _tail(data, lines=6):
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data or "")
    return "\n".join(line for line in text.strip().splitlines()[-lines:])


def ssh_failure(stderr):
    """Turn an OpenSSH connection failure into the one thing the user can do about it."""
    text = _tail(stderr)
    low = text.lower()
    if "host key verification failed" in low or "no matching host key" in low:
        hint = "This host's key isn't known yet. Connect once from Terminal to confirm it, then retry."
    elif "permission denied" in low or "passphrase" in low:
        hint = ("SSH needs a password or key passphrase the app can't type. Add your key to the SSH "
                "agent (ssh-add --apple-use-keychain <key>), then retry.")
    elif "could not resolve" in low or "name or service not known" in low:
        hint = "This host name doesn't resolve. Check the HostName in your SSH config."
    elif "kex_exchange_identification" in low or "banner exchange" in low or "connection reset" in low:
        hint = ("The server dropped the SSH connection before it started. Its SSH service may be overloaded "
                "or stuck, or a firewall such as fail2ban may be blocking this Mac. Check it from your "
                "provider's web console, then retry.")
    elif "timed out" in low or "connection refused" in low or "no route" in low:
        hint = "The server didn't answer on its SSH port. Check that it is running, then retry."
    else:
        hint = "SSH could not connect. Try ssh from Terminal to see what it needs, then retry."
    return hint + (" (" + text.splitlines()[-1] + ")" if text else "")


def app_archive(source):
    """The files deploy/install-vps.sh copies, as a gzip tar."""
    source = Path(source)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(source.glob("*.py")):
            tar.add(path, arcname=path.name)
        for folder in ("ui", "vendor", "deploy", "skills"):
            if (source / folder).is_dir():
                tar.add(source / folder, arcname=folder,
                        filter=lambda info: None if "/." in "/" + info.name or "__pycache__" in info.name else info)
    return buffer.getvalue()


def firewall_rule(hostname):
    """The narrowest ufw rule that lets this Mac reach the sync port at the address it pairs with."""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if (address and address in ipaddress.ip_network("100.64.0.0/10")) or hostname.endswith(".ts.net"):
        return f"allow in on tailscale0 to any port {SYNC_PORT} proto tcp", "on Tailscale only"
    lan = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd00::/8")
    if address and any(address in ipaddress.ip_network(n) for n in lan if ipaddress.ip_network(n).version == address.version):
        network = ipaddress.ip_network(f"{address}/{16 if address.version == 4 else 64}", strict=False)
        return f"allow from {network} to any port {SYNC_PORT} proto tcp", f"from {network} only"
    return f"allow {SYNC_PORT}/tcp", "from anywhere (paired instances only, mutual TLS)"


class Remote:
    """One multiplexed SSH connection to the chosen host."""

    def __init__(self, host, ssh="ssh"):
        self.host = valid_host(host)
        self.ssh = ssh
        # Short private path: a Unix socket path is limited to about 100 bytes on macOS.
        self.dir = tempfile.mkdtemp(prefix="mma-", dir="/tmp" if os.path.isdir("/tmp") else None)
        self.sudo = None  # "", "-n" or "-S"
        self.password = None

    def base(self):
        return [self.ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                "-o", "ServerAliveInterval=15", "-o", "ControlMaster=auto",
                "-o", "ControlPath=" + os.path.join(self.dir, "c"), "-o", "ControlPersist=300"]

    def run(self, command, data=b"", timeout=120):
        try:
            done = subprocess.run(self.base() + [self.host, command], input=data, capture_output=True,
                                  timeout=timeout, start_new_session=True)
        except subprocess.TimeoutExpired:
            raise SetupError("The server stopped responding. Retry when it is reachable.") from None
        except OSError:
            raise SetupError("Could not run ssh. Install OpenSSH and try again.") from None
        if done.returncode == 255:
            raise SetupError(ssh_failure(done.stderr))
        return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr

    def root(self, script, timeout=600):
        """Run a shell script as root. A password goes to sudo on stdin, never in arguments."""
        if self.sudo == "":
            return self.run("sh -c " + shlex.quote(script), timeout=timeout)
        if self.sudo == "-n":
            return self.run("sudo -n sh -c " + shlex.quote(script), timeout=timeout)
        # -k ignores a cached timestamp, so sudo always consumes the password line itself.
        return self.run("sudo -S -k -p '' sh -c " + shlex.quote(script),
                        data=(self.password or "").encode() + b"\n", timeout=timeout)

    def forward(self, port):
        """A socket connected to the server's loopback port through the SSH connection (ssh -W)."""
        mine, theirs = socket.socketpair()
        proc = subprocess.Popen(self.base() + ["-W", f"127.0.0.1:{port}", self.host], stdin=theirs,
                                stdout=theirs, stderr=subprocess.DEVNULL, start_new_session=True)
        theirs.close()
        return mine, proc

    def http(self, port, method, path, headers=None, body=None):
        sock, proc = self.forward(port)
        try:
            sock.settimeout(30)
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            conn.sock = sock
            conn.request(method, path, body=body, headers=headers or {})
            reply = conn.getresponse()
            return reply.status, reply.getheaders(), reply.read(2_000_000)
        except (OSError, http.client.HTTPException):
            raise SetupError("Could not reach the server's dashboard through SSH.") from None
        finally:
            sock.close()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def close(self):
        try:
            subprocess.run(self.base() + ["-O", "exit", self.host], capture_output=True, timeout=10,
                           stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(self.dir, ignore_errors=True)


def pairing_code(remote):
    """Ask the server's own dashboard for a pairing code, the way a browser would, through SSH."""
    script = (f"f={LAUNCH_FILE}; old=$(cat \"$f\" 2>/dev/null); systemctl kill --kill-whom=main -s USR1 {SERVICE_USER} || exit 1; "
              "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do sleep 0.5; "
              "new=$(cat \"$f\" 2>/dev/null); if [ -n \"$new\" ] && [ \"$new\" != \"$old\" ]; then "
              "echo \"$new\"; exit 0; fi; done; exit 1")
    status, out, _ = remote.root(script, timeout=60)
    url = urlsplit(out.strip().splitlines()[-1] if out.strip() else "")
    if status or url.hostname != "127.0.0.1" or not url.port or not url.path.startswith("/launch/"):
        raise SetupError("The server's service didn't hand out a dashboard link. Check journalctl -u memyselfai.")
    port = url.port
    status, headers, _ = remote.http(port, "GET", url.path)
    cookie = next((v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie"
                   and v.startswith("memyself_session=")), "")
    if status != 303 or not cookie:
        raise SetupError("The server's dashboard refused the one-time link. Retry.")
    status, _, page = remote.http(port, "GET", "/", {"Cookie": cookie})
    token = re.search(rb'name="app-token" content="([^"]+)"', page or b"")
    if status != 200 or not token:
        raise SetupError("The server's dashboard didn't open a session. Retry.")
    body = json.dumps({"op": "instance_code"}).encode()
    status, _, data = remote.http(port, "POST", "/api/action", {
        "Cookie": cookie, "X-App-Token": token.group(1).decode(), "Content-Type": "application/json",
        "Origin": f"http://127.0.0.1:{port}"}, body)
    try:
        result = json.loads(data)
    except ValueError:
        result = {}
    if status != 200 or not result.get("code"):
        raise SetupError(result.get("error") or "The server didn't create a pairing code. Is its sync port off?")
    return result["code"]


class VpsSetup:
    """One background setup run at a time, with progress the dashboard can show."""

    def __init__(self, instances, settings, log, source, ssh="ssh", home=None, open_terminal=None, on_done=None):
        self.instances = instances
        self.settings = settings
        self.log = log
        self.source = Path(source)
        self.ssh = ssh
        self.home = home
        self.open_terminal = open_terminal
        self.on_done = on_done
        self.lock = threading.Lock()
        self.need_password = threading.Event()
        self.answered = threading.Event()
        self.cancelled = threading.Event()
        self.thread = None
        self.run_state = None
        self.pending_password = None

    # Dashboard

    def hosts(self):
        try:
            return ssh_hosts(self.home)
        except OSError:
            return []

    def state(self):
        with self.lock:
            run = json.loads(json.dumps(self.run_state)) if self.run_state else None
        return dict(hosts=self.hosts(), run=run)

    def start(self, host):
        host = valid_host(host)
        with self.lock:
            # A finished run may still be closing its SSH connection; its own control socket is separate.
            if self.run_state and self.run_state["status"] in {"running", "needs_password"}:
                raise ValueError("A server setup is already running.")
            self.cancelled.clear()
            self.answered.clear()
            self.pending_password = None
            self.run_state = dict(host=host, status="running", error="", started=time.time(),
                                  address="", agent=self.provider(), password_error="",
                                  steps=[dict(id=k, label=v, status="pending", detail="") for k, v in STEPS])
            self.thread = threading.Thread(target=self._run, args=(host,), daemon=True, name="vps-setup")
            self.thread.start()

    def password(self, value):
        if not isinstance(value, str) or not value or len(value) > 1000 or "\n" in value:
            raise ValueError("Enter the sudo password for this server.")
        with self.lock:
            if not self.run_state or self.run_state["status"] != "needs_password":
                raise ValueError("The setup isn't waiting for a password.")
            self.pending_password = value
            self.run_state["status"] = "running"
        self.answered.set()

    def cancel(self):
        self.cancelled.set()
        self.answered.set()

    def sign_in(self, host=None):
        """Open Terminal on the Mac with the agent sign-in for the service user on the server.

        Uses this session's finished run, or the host remembered from an earlier one."""
        with self.lock:
            run = self.run_state
        if run and run["status"] == "done":
            host = run["host"]
        if not host:
            raise ValueError("Set up the server first.")
        agent = self.provider()
        command = (f"ssh -t {shlex.quote(valid_host(host))} "
                   + shlex.quote(f"sudo -u {SERVICE_USER} -i {SIGN_IN[agent]}"))
        if self.open_terminal and self.open_terminal(command):
            return {"command": command, "opened": True}
        return {"command": command, "opened": False}

    def host_for(self, address):
        """The SSH host in the user's config that reaches a paired server's address, if any."""
        for host in self.hosts():
            try:
                if f"{resolve(host, self.ssh)['hostname']}:{SYNC_PORT}" == address:
                    return host
            except (SetupError, ValueError):
                continue
        return None

    def provider(self):
        provider = (self.settings() or {}).get("provider")
        return provider if provider in PACKAGES else "claude"

    # Run

    def _step(self, key, status, detail=""):
        with self.lock:
            for step in self.run_state["steps"]:
                if step["id"] == key:
                    step.update(status=status, detail=detail)
                    if status == "running":
                        step["started"] = time.time()
                    elif step.get("started") and status in {"done", "failed"}:
                        step["seconds"] = round(time.time() - step["started"], 1)

    def _set(self, **fields):
        with self.lock:
            self.run_state.update(fields)

    def _check(self):
        if self.cancelled.is_set():
            raise Cancelled()

    def _run(self, host):
        remote = None
        current = "connect"
        try:
            target = resolve(host, self.ssh)
            self._set(address=f"{target['hostname']}:{SYNC_PORT}")
            remote = Remote(host, self.ssh)
            for current, _ in STEPS:
                self._check()
                self._step(current, "running")
                detail = getattr(self, "_" + current)(remote, target)
                self._step(current, "done", detail or "")
            self._set(status="done")
            if self.on_done:
                self.on_done(host)
            self.log("Server set up", f"{host} is installed and paired. Sign in its task agent to let it run tasks.")
        except Cancelled:
            self._step(current, "pending")
            self._set(status="cancelled")
        except SetupError as exc:
            self._step(current, "failed", str(exc))
            self._set(status="failed", error=str(exc))
            self.log("Server setup failed", f"{host}: {exc}")
        except Exception as exc:  # keep the dashboard informative on anything unexpected
            self._step(current, "failed", "Unexpected error.")
            self._set(status="failed", error=f"Unexpected error: {exc}")
            self.log("Server setup failed", f"{host}: {exc}")
        finally:
            self.pending_password = None
            if remote:
                remote.password = None
                remote.close()

    def _connect(self, remote, target):
        probe = ("echo os=$(uname -s); echo uid=$(id -u); "
                 "[ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1 && echo systemd=1; "
                 "command -v sudo >/dev/null 2>&1 && echo sudo=1; "
                 "sudo -n true >/dev/null 2>&1 && echo nopasswd=1; "
                 f"[ -f /etc/systemd/system/{SERVICE_USER}.service ] && echo installed=1; true")
        status, out, err = remote.run(probe, timeout=60)
        facts = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        if status or "os" not in facts:
            raise SetupError("The server didn't answer the setup checks. " + _tail(err, 2))
        if facts["os"] != "Linux" or not facts.get("systemd"):
            raise SetupError("This server can't run the automatic setup: it needs Linux with systemd. "
                             "Use the Docker steps in the VPS guide instead.")
        if facts.get("uid") == "0":
            remote.sudo = ""
        elif facts.get("nopasswd"):
            remote.sudo = "-n"
        elif facts.get("sudo"):
            remote.sudo = "-S"
            self._ask_password(remote)
        else:
            raise SetupError("The SSH user is not root and has no sudo. Use a host entry that logs in "
                             "as root or as an administrator.")
        login = "root" if remote.sudo == "" else ("password-free sudo" if remote.sudo == "-n" else "sudo")
        return f"{target['hostname']} · {login}" + (" · updating the existing install" if facts.get("installed") else "")

    def _ask_password(self, remote):
        error = ""
        for _ in range(3):
            self.answered.clear()
            self._set(status="needs_password", password_error=error)
            self.answered.wait()
            self._check()
            remote.password = self.pending_password
            self.pending_password = None
            status, _, _ = remote.root("true", timeout=60)
            if not status:
                self._set(status="running", password_error="")
                return
            error = "That password didn't work. Try again."
        raise SetupError("sudo refused the password three times.")

    def _copy(self, remote, target):
        archive = app_archive(self.source)
        status, _, err = remote.run(
            f'rm -rf "$HOME/{REMOTE_DIR}" && mkdir -p "$HOME/{REMOTE_DIR}" && tar -xzf - -C "$HOME/{REMOTE_DIR}"',
            data=archive, timeout=300)
        if status:
            raise SetupError("Copying the app failed: " + (_tail(err, 3) or "tar error"))
        return f"{len(archive) // 1024} KB"

    def _install(self, remote, target):
        _, home, _ = remote.run('printf %s "$HOME"', timeout=30)
        folder = shlex.quote(home.strip() + "/" + REMOTE_DIR)
        rule, scope = firewall_rule(target["hostname"])
        script = (f"cd {folder} && sh deploy/install-vps.sh && "
                  f"if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q 'Status: active'; then "
                  f"ufw {rule} >/dev/null && echo ufw=opened; fi")
        status, out, err = remote.root(script, timeout=1200)
        remote.run(f'rm -rf "$HOME/{REMOTE_DIR}"', timeout=60)
        if status:
            raise SetupError("The installer failed: " + (_tail(err, 4) or _tail(out, 4) or f"exit {status}"))
        return f"Firewall: allowed port {SYNC_PORT} {scope}" if "ufw=opened" in out else ""

    def _agent(self, remote, target):
        agent = self.provider()
        binary = "claude" if agent == "claude" else "codex"
        inner = (f"command -v {binary} >/dev/null 2>&1 && echo present || "
                 f"npm install -g --no-fund --no-audit {PACKAGES[agent]} >/dev/null")
        status, out, err = remote.root(f"sudo -u {SERVICE_USER} -H sh -lc {shlex.quote(inner)}", timeout=900)
        if status:
            raise SetupError("Installing the task agent failed: " + (_tail(err, 3) or f"exit {status}"))
        name = "Claude Code" if agent == "claude" else "Codex CLI"
        return name + (" already installed" if "present" in out else " installed")

    def _wait(self, remote, target):
        script = (f"for i in $(seq 30); do systemctl is-active --quiet {SERVICE_USER} && exit 0; "
                  "sleep 2; done; exit 1")
        status, _, _ = remote.root(script, timeout=120)
        if status:
            raise SetupError("The service didn't start within 60 seconds. Check journalctl -u memyselfai on the server.")
        return "Running"

    def _pair(self, remote, target):
        address = f"{target['hostname']}:{SYNC_PORT}"
        existing = next((p for p in self.instances.peers()
                         if p.get("address") == address and p.get("trusted") and not p.get("removed")), None)
        if existing:
            return f"Already paired with {existing['name']}"
        code = pairing_code(remote)
        self._check()
        try:
            info = self.instances.join(address, code)
        except (OSError, socket.timeout):
            raise SetupError(f"This Mac can't reach {address}. Allow TCP port {SYNC_PORT} in your "
                             "provider's firewall, then retry.") from None
        except Exception as exc:
            raise SetupError(f"Pairing with {address} failed: {exc}") from None
        self.log("Instance paired", f"{info['name']} ({info['platform']}) through server setup.")
        return f"Paired with {info['name']}"
