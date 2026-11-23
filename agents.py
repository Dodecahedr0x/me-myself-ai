"""Shared setup for every agent harness: Claude Code, Codex CLI and the Hermes orchestrator.

One subscription sign-in per instance powers both the task agents and Hermes. This module
detects installation and sign-in state without starting inference, writes Hermes' private
configuration, and builds the commands used to sign in. See docs/specs/agents-and-app-mcp.md.
"""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time

import worker

HARNESSES = ("claude", "codex", "hermes")
SUBSCRIPTIONS = ("claude", "codex")
# Hermes inference provider for each subscription.
HERMES_PROVIDER = {"claude": "anthropic", "codex": "openai-codex"}
HERMES_MIN_VERSION = (0, 13, 0)
# Hermes toolsets the orchestrator may use. Terminal, file, browser, web, code execution,
# messaging, cron and delegation stay off. The app MCP server is the only way to act.
HERMES_TOOLSETS = ("memory", "todo", "session_search", "memyselfai")
CHECK_TIMEOUT = 20
CACHE_SECONDS = 30

LABELS = {"claude": "Claude Code", "codex": "Codex CLI", "hermes": "Hermes"}
ROLES = {"claude": "Task agents", "codex": "Task agents", "hermes": "Orchestrator"}


def _run(args, env=None):
    try:
        done = subprocess.run(
            args,
            capture_output=True,
            timeout=CHECK_TIMEOUT,
            env=env,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


def executable(name):
    if name in worker.PROVIDERS:
        return worker._executable(name)
    directories = [p for p in os.get_exec_path() if os.path.isabs(p)]
    directories.extend([str(Path.home() / ".local/bin"), *worker.DESKTOP_CLI_DIRS])
    return shutil.which(name, path=os.pathsep.join(directories))


def _version(text):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


def hermes_home(root):
    return Path(root) / "hermes"


def hermes_env(root, binary=None):
    env = {
        "HOME": str(Path.home()),
        "PATH": os.pathsep.join(
            ([str(Path(binary).parent)] if binary else []) + ["/opt/homebrew/bin", "/usr/local/bin", os.defpath]
        ),
        "HERMES_HOME": str(hermes_home(root)),
        "NO_COLOR": "1",
    }
    return env


def claude_status(binary):
    result = _run([binary, "--version"])
    version = _version(result[1]) if result and result[0] == 0 else None
    result = _run([binary, "auth", "status", "--json"])
    if not result or result[0] not in (0, 1):
        return version, None, "Could not read the Claude Code sign-in state."
    try:
        signed_in = bool(json.loads(result[1]).get("loggedIn"))
    except (ValueError, AttributeError):
        return version, None, "Could not read the Claude Code sign-in state."
    return version, signed_in, ""


def codex_status(binary):
    result = _run([binary, "--version"])
    version = _version(result[1]) if result and result[0] == 0 else None
    result = _run([binary, "login", "status"])
    if not result:
        return version, None, "Could not read the Codex sign-in state."
    text = (result[1] + result[2]).strip()
    return version, result[0] == 0 and text.startswith("Logged in"), ""


def hermes_status(binary, root, subscription, harness_states):
    result = _run([binary, "--version"], env=hermes_env(root, binary))
    version = _version(result[1]) if result and result[0] == 0 else None
    if subscription == "claude":
        # Hermes reads Claude Code's own login when its provider is anthropic.
        # Avoid asking Hermes here: it would read the Keychain and could prompt the user.
        return version, harness_states.get("claude") == "ready", ""
    result = _run([binary, "auth", "status", HERMES_PROVIDER[subscription]], env=hermes_env(root, binary))
    if not result:
        return version, None, "Could not read the Hermes sign-in state."
    return version, result[0] == 0 and "logged in" in result[1] and "logged out" not in result[1], ""


def sign_in_command(name, root, subscription):
    """The interactive command that signs a harness in. It runs in the user's terminal."""
    binary = executable(name) or name
    if name == "claude":
        return [binary, "auth", "login"]
    if name == "codex":
        return [binary, "login"]
    if subscription == "claude":
        return [executable("claude") or "claude", "auth", "login"]
    return ["env", "HERMES_HOME=" + str(hermes_home(root)), binary, "auth", "add", HERMES_PROVIDER[subscription]]


def _state(installed, version, minimum, signed_in, error):
    if not installed:
        return "not_installed"
    if minimum and version and version < minimum:
        return "unsupported_version"
    if error or signed_in is None:
        return "unknown"
    return "ready" if signed_in else "sign_in"


class Agents:
    """Cached readiness of every harness on this instance."""

    def __init__(self, root, settings):
        self.root = Path(root)
        self.settings = settings
        self.lock = threading.Lock()
        self.cache = None

    def subscription(self):
        s = self.settings()
        chosen = s.get("orchestrator_subscription") or s.get("provider") or "claude"
        return chosen if chosen in SUBSCRIPTIONS else "claude"

    def inventory(self, refresh=False):
        with self.lock:
            if not refresh and self.cache and time.monotonic() - self.cache[0] < CACHE_SECONDS:
                return self.cache[1]
            subscription = self.subscription()
            rows = []
            states = {}
            for name in HARNESSES:
                binary = executable(name)
                version = signed_in = None
                error = ""
                if binary and name == "claude":
                    version, signed_in, error = claude_status(binary)
                elif binary and name == "codex":
                    version, signed_in, error = codex_status(binary)
                elif binary:
                    version, signed_in, error = hermes_status(binary, self.root, subscription, states)
                state = _state(bool(binary), version, HERMES_MIN_VERSION if name == "hermes" else None,
                               signed_in, error)
                states[name] = state
                command = sign_in_command(name, self.root, subscription)
                rows.append(dict(
                    id=name,
                    label=LABELS[name],
                    role=ROLES[name],
                    installed=bool(binary),
                    version=".".join(map(str, version)) if version else "",
                    state=state,
                    detail=error,
                    sign_in=" ".join(shlex.quote(part) for part in command),
                    uses=subscription if name == "hermes" else name,
                ))
            data = dict(
                harnesses=rows,
                task_agent=self.settings().get("provider", "claude"),
                orchestrator_subscription=subscription,
            )
            self.cache = (time.monotonic(), data)
            return data

    def open_sign_in(self, name):
        """Start the interactive sign-in in Terminal on macOS, or return the command to run."""
        if name not in HARNESSES:
            raise ValueError("Unknown agent.")
        command = " ".join(shlex.quote(p) for p in sign_in_command(name, self.root, self.subscription()))
        with self.lock:
            self.cache = None
        if sys.platform != "darwin":
            return {"command": command, "opened": False}
        script = 'tell application "Terminal"\nactivate\ndo script ' + json.dumps(command) + "\nend tell"
        try:
            subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            return {"command": command, "opened": False}
        return {"command": command, "opened": True}

    def write_hermes_config(self, mcp_command, mcp_env):
        """Write Hermes' private config. The app owns this file and rewrites it on every start."""
        home = hermes_home(self.root)
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        provider = HERMES_PROVIDER[self.subscription()]
        config = {
            "model": {"provider": provider},
            "approvals": {"mode": "manual", "cron_mode": "deny", "unattended_mode": "deny",
                          "single_query_mode": "deny"},
            "mcp_servers": {
                "memyselfai": {"command": mcp_command[0], "args": mcp_command[1:], "env": mcp_env}
            },
        }
        # JSON is valid YAML, so no YAML library is needed.
        path = home / "config.yaml"
        temporary = home / "config.yaml.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        os.replace(temporary, path)
        return path

    def hermes_command(self, prompt):
        binary = executable("hermes")
        if not binary:
            raise ValueError("Hermes is not installed.")
        return [binary, "-z", prompt, "-t", ",".join(HERMES_TOOLSETS), "--ignore-rules"], hermes_env(
            self.root, binary
        )
