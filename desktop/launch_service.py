"""Start the bundled service and release its workspace if the desktop host exits."""

import fcntl
import hashlib
import os
from pathlib import Path
import runpy
import shutil
import signal
import sys
import threading


def watch_desktop():
    # The host alone holds the writing end. Even a force-quit closes the pipe.
    try:
        while os.read(0, 1024):
            pass
    finally:
        os.kill(os.getpid(), signal.SIGTERM)


def stable_resources(resources, data):
    """Run from a copy in the data folder, so deleting the app build cannot break running work.

    A build inside a temporary checkout may vanish while the app runs. See desktop-runtime.md.
    """
    contents = resources.parent
    seal = contents / "_CodeSignature" / "CodeResources"
    if not data or not seal.is_file():
        return resources
    tag = hashlib.sha256(seal.read_bytes()).hexdigest()[:16]
    Path(data).mkdir(parents=True, exist_ok=True)
    with (Path(data) / "app.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return resources  # another copy owns this workspace; the service will say so and exit
        try:
            return _refresh(contents, Path(data) / "app-runtime", tag)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _refresh(contents, root, tag):
    target = root / tag
    if not (target / ".complete").is_file():
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{tag}.{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        for name in ("Resources", "Helpers"):
            if (contents / name).is_dir():
                shutil.copytree(contents / name, staging / name, symlinks=True)
        (staging / ".complete").touch()
        shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    # One service owns the data folder, so older copies are no longer in use.
    for old in root.iterdir():
        if old.name != tag:
            shutil.rmtree(old, ignore_errors=True)
    return target / "Resources"


def data_argument(argv):
    for i, arg in enumerate(argv):
        if arg == "--data" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--data="):
            return arg.split("=", 1)[1]
    return None


if __name__ == "__main__":
    # The service signals the host back once work is handed over before sleep.
    os.environ["MEMYSELFAI_HOST_PID"] = str(os.getppid())
    threading.Thread(target=watch_desktop, daemon=True).start()
    resources = Path(__file__).resolve().parent
    try:
        resources = stable_resources(resources, data_argument(sys.argv))
    except OSError as error:
        print(f"Running from the app bundle; the stable copy failed: {error}", file=sys.stderr)
    sys.path.insert(0, str(resources))
    runpy.run_path(str(resources / "server.py"), run_name="__main__")
