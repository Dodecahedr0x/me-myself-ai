"""Desktop notifications when a task newly needs the user or a dispatched task finishes.

The service decides what to notify and the desktop host posts it. See docs/specs/dashboard.md#notifications.
"""

import json
import os
from pathlib import Path
import time

# Statuses that need the user, as in the inbox. A question about an inaccessible checkout needs folder access.
NEEDS_YOU = {
    "review": "Needs your approval",
    "question": "Needs your answer",
    "failed": "Failed: retry or give instructions",
    "uncertain": "Check whether it was delivered",
}
FOLDER = "Needs folder access"
FINISHED = "Finished"
# A task first seen is new if created after the last check, less this allowance for sync delay.
GRACE = 600
# How often the last-check time is saved while nothing changes.
REFRESH = 60


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

    The file is not synced: each desktop instance keeps its own record of what it has shown. It also keeps
    `since`, when this instance last checked. A task seen for the first time notifies only if it was created
    after that, less a grace period for sync delay, so history that arrives by pairing or sync stays silent.
    """

    def __init__(self, path, sink=None, enabled=lambda: True, clock=time.time):
        self.path = Path(path)
        self.sink = sink
        self.enabled = enabled
        self.clock = clock
        self.seen = None
        self.since = 0.0

    def load(self):
        """The recorded states, or None when there is no usable record (first run, or unreadable)."""
        try:
            record = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or not isinstance(record.get("tasks"), dict):
            return None
        since = record.get("since")
        self.since = float(since) if isinstance(since, (int, float)) else 0.0
        return record["tasks"]

    def store(self, seen, since):
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps({"since": since, "tasks": seen}))
        os.replace(tmp, self.path)

    def check(self, tasks):
        """Return the notifications for tasks whose state changed since last seen, and post them."""
        if self.sink is None:
            return []
        now = self.clock()
        if self.seen is None:
            self.seen = self.load()
        first = self.seen is None
        previous = self.seen or {}
        current = {t["id"]: f'{t.get("status")}{":folder" if t.get("checkout_inaccessible") else ""}' for t in tasks}
        # Unchanged: nothing to post. The saved time is still refreshed, so it says when this instance last ran.
        if not first and current == previous and now - self.since < REFRESH:
            return []
        out = []
        if not first and self.enabled():
            for t in sorted(tasks, key=lambda t: t.get("created", 0)):
                title = heading(t)
                if not title or t.get("sample") or previous.get(t["id"]) == current[t["id"]]:
                    continue
                if t["id"] not in previous and t.get("created", 0) < self.since - GRACE:
                    continue  # Older history first seen now, for example from a newly paired instance.
                out.append(dict(task=t["id"], key=f'{t["id"]}:{t.get("version", 0)}', title=title,
                                body=(t.get("title") or "Task")[:200]))
        # Recorded before posting: a crash may lose a notification but never repeats one.
        # If the file cannot be written, memory still prevents repeats until the next start.
        try:
            self.store(current, now)
        except OSError:
            pass
        self.seen = current
        self.since = now
        for item in out:
            self.sink(item)
        return out
