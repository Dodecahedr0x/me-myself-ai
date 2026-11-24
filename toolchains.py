"""Toolchains and dependency fetches that task agents may ask for. See "Toolchains and dependencies" in
docs/specs/local-agent.md.

Installs read only from official hosts, verify published checksums and write only under the instance's own
toolchains folder. Fetches are fixed commands; the agent supplies only a folder inside the task worktree."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

GO_INDEX = "https://go.dev/dl/?mode=json"
GO_DOWNLOAD = "https://go.dev/dl/"
RUSTUP = "https://static.rust-lang.org/rustup/dist/{triple}/rustup-init"
NPM_REGISTRY = "https://registry.npmjs.org/"
MAX_DOWNLOAD = 400 * 1024 * 1024
MANIFESTS = {"go": "go.mod", "rust": "Cargo.toml", "npm": "package.json"}


class SetupError(Exception):
    pass


def _get(url, limit=MAX_DOWNLOAD):
    if not url.startswith(("https://go.dev/", "https://dl.google.com/", "https://static.rust-lang.org/")):
        raise SetupError("Downloads come only from official toolchain hosts.")
    request = urllib.request.Request(url, headers={"User-Agent": "MeMyselfAI-toolchains"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise SetupError("The download is larger than expected.")
    return data


def _arch():
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return "amd64", "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64", "aarch64"
    raise SetupError(f"No toolchain build for the {machine} processor.")


def _os():
    if sys.platform == "darwin":
        return "darwin", "apple-darwin"
    if sys.platform.startswith("linux"):
        return "linux", "unknown-linux-gnu"
    raise SetupError("Toolchains install on macOS and Linux only.")


def bin_paths(root):
    root = Path(root)
    return [str(p) for p in (root / "go" / "bin", root / "rust" / "cargo" / "bin") if p.is_dir()]


def install(root, name, get=_get):
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if name == "go":
        return _install_go(root, get)
    if name == "rust":
        return _install_rust(root, get)
    raise SetupError("Installable toolchains are go and rust.")


def _install_go(root, get):
    goos, _ = _os()
    goarch, _ = _arch()
    releases = json.loads(get(GO_INDEX, 4 * 1024 * 1024))
    release = next((r for r in releases if r.get("stable")), None)
    if not release:
        raise SetupError("go.dev listed no stable Go release.")
    file = next((f for f in release["files"] if f.get("os") == goos and f.get("arch") == goarch
                 and f.get("kind") == "archive"), None)
    if not file or not re.fullmatch(r"go[\w.-]+\.tar\.gz", file["filename"]) \
            or not re.fullmatch(r"[0-9a-f]{64}", file.get("sha256", "")):
        raise SetupError(f"go.dev has no Go archive for {goos}/{goarch}.")
    version = release["version"]
    if not re.fullmatch(r"go\d+(\.\d+)*", version):
        raise SetupError("go.dev returned an unexpected Go version.")
    target = root / version
    if not (target / "bin" / "go").exists():
        data = get(GO_DOWNLOAD + file["filename"])
        if hashlib.sha256(data).hexdigest() != file["sha256"]:
            raise SetupError("The Go download did not match its published checksum.")
        with tempfile.TemporaryDirectory(dir=root, prefix=".go-") as stage:
            archive = Path(stage) / "go.tar.gz"
            archive.write_bytes(data)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    if not (member.name == "go" or member.name.startswith("go/")) or ".." in Path(member.name).parts:
                        raise SetupError("The Go archive has a path outside its folder.")
                tar.extractall(stage, filter="data")
            (Path(stage) / "go").rename(target)
    _point(root / "go", version)
    return f"Installed {version} for {goos}/{goarch} in {target}"


def _install_rust(root, get):
    _, vendor = _os()
    _, cpu = _arch()
    triple = f"{cpu}-{vendor}"
    rust = root / "rust"
    env = {**os.environ, "RUSTUP_HOME": str(rust / "rustup"), "CARGO_HOME": str(rust / "cargo"),
           "RUSTUP_INIT_SKIP_PATH_CHECK": "yes"}
    if (rust / "cargo" / "bin" / "cargo").exists():
        return f"Rust is already installed in {rust}"
    url = RUSTUP.format(triple=triple)
    data = get(url)
    published = get(url + ".sha256", 4096).decode().split()
    if not published or hashlib.sha256(data).hexdigest() != published[0]:
        raise SetupError("The rustup download did not match its published checksum.")
    with tempfile.TemporaryDirectory(dir=root, prefix=".rust-") as stage:
        init = Path(stage) / "rustup-init"
        init.write_bytes(data)
        init.chmod(0o700)
        proc = subprocess.run([str(init), "-y", "--no-modify-path", "--profile", "minimal",
                               "--default-toolchain", "stable"], env=env, capture_output=True, text=True,
                              timeout=1200, stdin=subprocess.DEVNULL)
    if proc.returncode:
        shutil.rmtree(rust, ignore_errors=True)
        raise SetupError("rustup failed: " + (proc.stderr.strip().splitlines() or ["error"])[-1][:200])
    return f"Installed stable Rust for {triple} in {rust}"


def _point(link, name):
    staged = link.with_name(link.name + ".new")
    if staged.is_symlink() or staged.exists():
        staged.unlink()
    staged.symlink_to(name)
    os.replace(staged, link)


def cache_env(root, cache):
    """Environment for commands and fetches: installed toolchains first and the task's own dependency cache."""
    root, cache = Path(root), Path(cache)
    env = {
        "GOPATH": str(cache / "go"), "GOMODCACHE": str(cache / "go" / "pkg" / "mod"),
        "GOCACHE": str(cache / "go-build"), "GOFLAGS": "-modcacherw", "GOTELEMETRY": "off",
        "CARGO_HOME": str(cache / "cargo"), "npm_config_cache": str(cache / "npm"),
        "npm_config_update_notifier": "false", "npm_config_fund": "false", "npm_config_audit": "false",
    }
    if (root / "rust" / "rustup").is_dir():
        env["RUSTUP_HOME"] = str(root / "rust" / "rustup")
    return env


def parse(text, worktree):
    """A setup step from its text: ("install", name, None) or ("fetch", kind, folder)."""
    words = str(text).strip().split(None, 2)
    if len(words) == 2 and words[0] == "install" and words[1] in {"go", "rust"}:
        return "install", words[1], None
    if len(words) in (2, 3) and words[0] == "fetch" and words[1] in MANIFESTS:
        relative = words[2].strip() if len(words) == 3 else "."
        worktree = Path(worktree).resolve()
        folder = (worktree / relative).resolve()
        if folder != worktree and worktree not in folder.parents:
            raise SetupError("Fetch inside the task worktree.")
        if any(part.startswith(".") for part in folder.relative_to(worktree).parts):
            raise SetupError("Fetch in a project folder, not a hidden one.")
        if not (folder / MANIFESTS[words[1]]).is_file():
            raise SetupError(f"{relative} has no {MANIFESTS[words[1]]}.")
        return "fetch", words[1], folder
    raise SetupError("Setup steps are: install go, install rust, fetch go|rust|npm <folder>.")


def fetch_command(kind, folder):
    """The fixed command for a fetch. No repository code runs: nothing compiles and install scripts are off."""
    folder = Path(folder)
    if kind == "go":
        return "go mod download"
    if kind == "rust":
        return "cargo fetch"
    if kind == "npm":
        install = "ci" if (folder / "package-lock.json").is_file() else "install"
        return f"npm {install} --ignore-scripts --no-audit --no-fund --registry {NPM_REGISTRY}"
    raise SetupError("Unknown fetch kind.")
