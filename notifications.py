"""Desktop notifications when a task newly needs the user or a dispatched task finishes.

The service decides what to notify and the desktop host posts it. See docs/specs/dashboard.md#notifications.
"""

import json
import os
from pathlib import Path

# Statuses that need the user, as in the inbox. A question about an inaccessible checkout needs folder access.
NEEDS_YOU = {
    "review": "Needs your approval",
    "question": "Needs your answer",
    "failed": "Failed: retry or give instructions",
    "uncertain": "Check whether it was delivered",
}
FOLDER = "Needs folder access"
FINISHED = "Finished"


def heading(task):
    """What the notification says about a task now, or None when its state is not worth a notification."""
    status = task.get("status")
    if status == "question" and task.get("checkout_inaccessible"):
        return FOLDER
    if status in NEEDS_YOU:
        return NEEDS_YOU[status]
    if status == "done" and task.get("origin") == "orchestrator":
        return FINISHED
    return None


class Notifier:
    """Remembers the state each task was last seen in, in a local file, so a state is notified once.

    The file is not synced: each desktop instance keeps its own record of what it has shown.
    """

    def __init__(self, path, sink=None, enabled=lambda: True):
        self.path = Path(path)
        self.sink = sink
        self.enabled = enabled
        self.seen = None

    def load(self):
        try:
            seen = json.loads(self.path.read_text())
            return seen if isinstance(seen, dict) else {}
        except FileNotFoundError:
            return None  # First run: record the current states without notifying.
        except (OSError, ValueError):
            return {}

    def store(self, seen):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(seen))
        os.replace(tmp, self.path)

    def check(self, tasks):
        """Return the notifications for tasks whose state changed since last seen, and post them."""
        if self.sink is None:
            return []
        if self.seen is None:
            self.seen = self.load()
        first = self.seen is None
        previous = self.seen or {}
        current = {t["id"]: f'{t.get("status")}{":folder" if t.get("checkout_inaccessible") else ""}' for t in tasks}
        if not first and current == previous:
            return []
        out = []
        if not first and self.enabled():
            for t in sorted(tasks, key=lambda t: t.get("created", 0)):
                title = heading(t)
                if title and previous.get(t["id"]) != current[t["id"]]:
                    out.append(dict(task=t["id"], key=f'{t["id"]}:{t.get("version", 0)}', title=title,
                                    body=(t.get("title") or "Task")[:200]))
        # Recorded before posting: a crash may lose a notification but never repeats one.
        # If the file cannot be written, memory still prevents repeats until the next start.
        try:
            self.store(current)
        except OSError:
            pass
        self.seen = current
        for item in out:
            self.sink(item)
        return out
