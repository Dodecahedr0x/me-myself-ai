"""Start the bundled service and release its workspace if the desktop host exits."""

import os
from pathlib import Path
import runpy
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


if __name__ == "__main__":
    # The service signals the host back once work is handed over before sleep.
    os.environ["MEMYSELFAI_HOST_PID"] = str(os.getppid())
    threading.Thread(target=watch_desktop, daemon=True).start()
    resources = Path(__file__).resolve().parent
    sys.path.insert(0, str(resources))
    runpy.run_path(str(resources / "server.py"), run_name="__main__")
