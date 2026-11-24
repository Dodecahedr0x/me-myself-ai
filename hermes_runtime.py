"""The app's own Hermes: a pinned, checksum-verified install in the workspace data directory.

Everything lives under <data>/hermes-runtime (uv, a uv-managed Python, the Hermes source and its
locked environment) and Hermes' home is <data>/hermes. A Hermes the user installed separately, and
its ~/.hermes, are never used or changed. The `memyselfai-hermes` command opens this copy directly,
for example `memyselfai-hermes dashboard`. See docs/specs/hermes-orchestrator.md#install.
"""

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

# Tested with this MeMyselfAI release. Changing a pin needs a new checksum and a test run.
# 0.23.3 is the first release whose Claude subscription requests count against the plan: earlier ones
# are classed as a third-party app and drawn from extra usage.
HERMES_VERSION = "0.23.3"
HERMES_TAG = "v" + HERMES_VERSION
HERMES_COMMIT = "caafe5389ae585ef77387a962aee05b1bcf17029"
HERMES_URL = f"https://github.com/NousResearch/hermes-agent/archive/refs/tags/{HERMES_TAG}.tar.gz"
HERMES_SHA256 = "061442bf28ee1abd47ef7d6884c5fd906ee0f478b4c3813a5b899ba16544f150"
UV_VERSION = "0.13.9"
UV_BUILDS = {
    ("darwin", "arm64"): ("aarch64-apple-darwin", "5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca"),
    ("darwin", "x86_64"): ("x86_64-apple-darwin", "617a3211261b0fc739562f0071aa34e9739797c510208ede8e2977c30903d041"),
    ("linux", "x86_64"): ("x86_64-unknown-linux-gnu", "a7fbaff2b9a271d85c1bdb540941090fd1ce011797b4abeb48e4dcd39c2471d5"),
    ("linux", "aarch64"): ("aarch64-unknown-linux-gnu", "0797542018e541a9288855df555c8077eed06f7a47d206362030480e8ecc9f3d"),
}
PYTHON = "3.14"  # Hermes' lockfile supports only 3.14.
# anthropic talks to Claude; mcp reaches the app server; web serves `hermes dashboard`.
EXTRAS = ("anthropic", "mcp", "web")
MAX_DOWNLOAD = 200 * 1024 * 1024
STEP_TIMEOUT = 30 * 60
COMMAND = "memyselfai-hermes"


def private_environment(home):
    """Hermes' home, plus its per-user state folder (gateway locks), which would otherwise be shared
    with any other Hermes under ~/.local/state."""
    return {"HERMES_HOME": str(home), "XDG_STATE_HOME": str(Path(home) / "xdg-state")}


class InstallError(RuntimeError):
    """A failed install step, shown in Settings → Agents with a retry."""


def download(url, destination, sha256):
    digest = hashlib.sha256()
    size = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response, open(destination, "wb") as out:
            while chunk := response.read(1 << 20):
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise InstallError(f"The download from {url} is larger than expected.")
                digest.update(chunk)
                out.write(chunk)
    except OSError as exc:
        raise InstallError(f"Could not download {url}: {exc}") from None
    if digest.hexdigest() != sha256:
        raise InstallError(f"The download from {url} failed its checksum. Nothing was installed.")


def extract(archive, destination):
    """Extract a tar archive, refusing links and paths that leave the destination."""
    destination = Path(destination).resolve()
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if destination not in target.parents and target != destination:
                raise InstallError("The archive contains a path outside its folder.")
            if member.islnk() or (member.issym() and not (target.parent / member.linkname).resolve().is_relative_to(destination)):
                raise InstallError("The archive contains a link outside its folder.")
        tar.extractall(destination, **({"filter": "tar"} if hasattr(tarfile, "tar_filter") else {}))


class Runtime:
    def __init__(self, root, fetch=download):
        self.root = Path(root)
        self.dir = self.root / "hermes-runtime"
        self.home = self.root / "hermes"
        self.fetch = fetch
        self.lock = threading.Lock()
        self.thread = None

    # Where things are

    @property
    def source(self):
        return self.dir / ("hermes-" + HERMES_TAG)

    @property
    def wrapper(self):
        return self.dir / COMMAND

    def binary(self):
        path = self.dir / "current" / ".venv" / "bin" / "hermes"
        return str(path) if os.access(path, os.X_OK) else None

    def state(self):
        try:
            return json.loads((self.dir / "state.json").read_text())
        except (OSError, ValueError):
            return {"status": "missing"}

    def _record(self, **state):
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.dir / "state.json.tmp"
        temporary.write_text(json.dumps({**state, "at": time.time()}))
        os.replace(temporary, self.dir / "state.json")

    def installed(self):
        state = self.state()
        return self.binary() is not None and state.get("version") == HERMES_VERSION and state.get("extras") == list(EXTRAS)

    def environment(self):
        return {**os.environ, **private_environment(self.home)}

    # Installing

    def ensure_async(self):
        """Install or update in the background. Safe to call on every start and from Retry."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self.ensure, daemon=True, name="hermes-install")
            self.thread.start()

    def ensure(self):
        try:
            if not self.installed():
                self.install()
            self.write_command()
        except Exception as exc:  # Shown in Settings → Agents; the previous version keeps running.
            self._record(status="failed", version=self.state().get("version", ""), error=str(exc)[:500])

    def install(self):
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(self.dir / "install.lock", "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)  # Another process (the VPS installer) may be installing.
            if self.installed():
                return
            previous = self.state().get("version", "")
            self._record(status="installing", version=previous, step="Downloading uv")
            uv = self.install_uv()
            self._record(status="installing", version=previous, step="Downloading Hermes " + HERMES_VERSION)
            self.install_source()
            self._record(status="installing", version=previous, step="Installing Python and dependencies")
            self.run([uv, "sync", "--frozen", "--no-dev", "--python", PYTHON,
                      *[arg for extra in EXTRAS for arg in ("--extra", extra)]], cwd=self.source)
            self.write_stamp()
            hermes = self.source / ".venv" / "bin" / "hermes"
            reported = self.run([str(hermes), "--version"], cwd=self.dir, env=self.environment())
            if not reported.startswith(f"Hermes Agent v{HERMES_VERSION} "):
                raise InstallError(f"The installed Hermes reported {reported.strip()[:80]!r}.")
            link = self.dir / "current.tmp"
            with contextlib.suppress(FileNotFoundError):
                link.unlink()
            link.symlink_to(self.source.name)
            os.replace(link, self.dir / "current")
            for old in self.dir.glob("hermes-v*"):
                if old != self.source:
                    shutil.rmtree(old, ignore_errors=True)
            shutil.rmtree(self.dir / "cache", ignore_errors=True)  # Only needed while installing.
            self._record(status="ready", version=HERMES_VERSION, extras=list(EXTRAS), error="")

    def install_uv(self):
        machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
        key = (sys.platform, "aarch64" if sys.platform == "linux" and machine == "arm64" else machine)
        if key not in UV_BUILDS:
            raise InstallError(f"Hermes cannot be installed on {sys.platform} {platform.machine()}.")
        target, sha256 = UV_BUILDS[key]
        folder = self.dir / ("uv-" + UV_VERSION)
        uv = folder / "uv"
        if os.access(uv, os.X_OK):
            return str(uv)
        with tempfile.TemporaryDirectory(dir=self.dir) as temporary:
            archive = Path(temporary) / "uv.tar.gz"
            self.fetch(f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-{target}.tar.gz",
                       archive, sha256)
            extract(archive, temporary)
            folder.mkdir(mode=0o700, exist_ok=True)
            shutil.move(str(Path(temporary) / f"uv-{target}" / "uv"), uv)
        uv.chmod(0o700)
        return str(uv)

    def install_source(self):
        if (self.source / "uv.lock").exists():
            return
        partial = self.dir / (self.source.name + ".partial")
        shutil.rmtree(partial, ignore_errors=True)
        partial.mkdir(mode=0o700)
        with tempfile.TemporaryDirectory(dir=self.dir) as temporary:
            archive = Path(temporary) / "hermes.tar.gz"
            self.fetch(HERMES_URL, archive, HERMES_SHA256)
            extract(archive, partial)
        inner = next(partial.iterdir())  # GitHub archives hold one top-level folder.
        shutil.rmtree(self.source, ignore_errors=True)
        inner.rename(self.source)
        shutil.rmtree(partial, ignore_errors=True)

    def write_stamp(self):
        """Hermes reads its version from this stamp; a source archive has no git history to read.

        `external` tells `hermes update` that MeMyselfAI, not Hermes, replaces this copy.
        """
        stamp = {"schemaVersion": 2, "commit": HERMES_COMMIT, "commitDate": 0, "branch": None,
                 "builtAt": "1970-01-01T00:00:00+00:00", "dirty": False, "source": "memyselfai",
                 "distribution": None, "updateMechanism": "external", "baseVersion": HERMES_VERSION,
                 "displayVersion": HERMES_VERSION, "distance": 0, "payload": "bootstrap", "tag": None}
        (self.source / "install-stamp.json").write_text(json.dumps(stamp, indent=2) + "\n")

    def run(self, command, cwd, env=None):
        # uv keeps its cache and Python under the runtime folder and ignores the user's uv settings.
        env = {**(env or os.environ), "UV_CACHE_DIR": str(self.dir / "cache"),
               "UV_PYTHON_INSTALL_DIR": str(self.dir / "python"), "UV_PYTHON_PREFERENCE": "only-managed",
               "UV_NO_CONFIG": "1", "UV_PROJECT_ENVIRONMENT": str(self.source / ".venv")}
        try:
            done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=STEP_TIMEOUT,
                                  stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise InstallError(f"{Path(command[0]).name} failed: {exc}") from None
        if done.returncode:
            tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise InstallError(f"{Path(command[0]).name} failed: " + (" ".join(tail) or f"status {done.returncode}"))
        return done.stdout.decode("utf-8", "replace")

    # The gateway

    def stop_gateway(self, wait=15):
        """Stop the gateway Hermes starts for one-shot runs.

        Hermes 0.23 runs every prompt through a gateway that outlives the run, keeps the MCP connection
        it opened at start and runs Hermes' own cron scheduler. The orchestrator stops it before and
        after each run, so each run starts fresh with that run's token and nothing runs in between.
        Only a gateway of this copy and this home is signalled.
        """
        try:
            record = json.loads((self.home / "gateway.pid").read_text())
            pid = int(record["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if record.get("hermes_home") != str(self.home) or not self._is_gateway(pid):
            return False
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and self._is_gateway(pid):
            time.sleep(0.2)
        if self._is_gateway(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        return True

    def _is_gateway(self, pid):
        try:
            done = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return False
        command = done.stdout.decode("utf-8", "replace")
        return " gateway run" in command and str(self.dir) in command

    # Direct access

    def write_command(self):
        """`memyselfai-hermes` runs this copy with its private home, for example `... dashboard`."""
        lines = [
            "#!/bin/sh",
            "# MeMyselfAI's own Hermes. It uses its private home and never touches ~/.hermes.",
            "# The app rewrites config.yaml on start; settings you change here may be replaced.",
            *[f"export {name}={shlex.quote(value)}" for name, value in private_environment(self.home).items()],
            "exec " + shlex.quote(str(self.dir / "current" / ".venv" / "bin" / "hermes")) + ' "$@"',
        ]
        temporary = self.wrapper.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n")
        temporary.chmod(0o755)
        os.replace(temporary, self.wrapper)
        # Also on the user's PATH, unless something else already has that name.
        link = Path.home() / ".local" / "bin" / COMMAND
        with contextlib.suppress(OSError):
            if link.is_symlink() or not link.exists():
                link.parent.mkdir(parents=True, exist_ok=True)
                temporary_link = link.with_name(COMMAND + ".tmp")
                with contextlib.suppress(FileNotFoundError):
                    temporary_link.unlink()
                temporary_link.symlink_to(self.wrapper)
                os.replace(temporary_link, link)

    def dashboard_command(self):
        return shlex.quote(str(self.wrapper)) + " dashboard"


if __name__ == "__main__":
    # The VPS installer runs this as the service user so Hermes is ready before the service starts.
    import argparse

    parser = argparse.ArgumentParser(description="Install MeMyselfAI's own Hermes.")
    parser.add_argument("--data", type=Path, required=True)
    runtime = Runtime(parser.parse_args().data)
    runtime.ensure()
    state = runtime.state()
    print(f"Hermes {state.get('version') or HERMES_VERSION}: {state.get('status')} {state.get('error', '')}".strip())
    sys.exit(0 if runtime.installed() else 1)
