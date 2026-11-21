"""Transactional local storage for the desktop service."""

import contextlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(
            self.root / "workspace.sqlite3", check_same_thread=False, isolation_level=None
        )
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS records (bucket TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(bucket,id))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS activity (seq INTEGER PRIMARY KEY, at REAL, kind TEXT, project TEXT, task TEXT, detail TEXT)"
        )

        with self.atomic():
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(activity)")}
            if "connection" not in columns:
                self.conn.execute("ALTER TABLE activity ADD COLUMN connection TEXT NOT NULL DEFAULT ''")

    @contextlib.contextmanager
    def atomic(self):
        with self.lock:
            outer = not self.conn.in_transaction
            if outer:
                self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                if outer:
                    self.conn.commit()
            except BaseException:
                if outer:
                    self.conn.rollback()
                raise

    def get(self, bucket, key):
        with self.lock:
            row = self.conn.execute(
                "SELECT data FROM records WHERE bucket=? AND id=?", (bucket, key)
            ).fetchone()
            return json.loads(row[0]) if row else None

    def rows(self, bucket):
        with self.lock:
            return [
                json.loads(row[0])
                for row in self.conn.execute(
                    "SELECT data FROM records WHERE bucket=? ORDER BY rowid", (bucket,)
                )
            ]

    def put(self, bucket, row):
        with self.lock:
            self.conn.execute(
                "INSERT INTO records VALUES (?,?,?) ON CONFLICT(bucket,id) DO UPDATE SET data=excluded.data",
                (bucket, row["id"], json.dumps(row)),
            )

    def delete(self, bucket, key):
        with self.lock:
            self.conn.execute("DELETE FROM records WHERE bucket=? AND id=?", (bucket, key))

    def log(self, kind, detail, project="", task="", connection=""):
        with self.lock:
            self.conn.execute(
                "INSERT INTO activity(at,kind,project,task,detail,connection) VALUES (?,?,?,?,?,?)",
                (time.time(), kind, project, task, str(detail), connection),
            )

    def dashboard_tasks(self):
        """Attach the original source independently of the current delivery target."""
        with self.lock:
            rows = self.conn.execute(
                """SELECT t.data, json_extract(c.data,'$.kind'),
                          json_extract(e.data,'$.raw.context.google_service'),
                          json_extract(e.data,'$.raw.account'), json_extract(e.data,'$.raw.context.repository'),
                          json_extract(e.data,'$.raw.context.channel'), json_extract(c.data,'$.label'),
                          json_extract(workspace.data,'$.label')
                     FROM records AS t
                LEFT JOIN records AS e ON e.bucket='events' AND e.id=json_extract(t.data,'$.event')
                LEFT JOIN records AS c ON c.bucket='connections' AND c.id=json_extract(e.data,'$.connection')
                LEFT JOIN records AS workspace ON workspace.bucket='connections'
                          AND workspace.id=json_extract(c.data,'$.slack_parent')
                    WHERE t.bucket='tasks'"""
            )
            tasks = []
            for data, kind, service, account, repository, channel, label, workspace in rows:
                task = json.loads(data)
                if kind or service or account or repository or channel:
                    task["origin"] = dict(
                        source=service or kind,
                        account=account,
                        repository=repository,
                        channel=(label or channel) if channel else None,
                        workspace=workspace,
                    )
                tasks.append(task)
            return tasks

    def history(self):
        with self.lock:
            cols = ("id", "at", "kind", "project", "task", "detail", "connection")
            return [
                dict(zip(cols, row))
                for row in self.conn.execute(
                    """SELECT a.seq,a.at,a.kind,a.project,a.task,a.detail,
                              coalesce(nullif(a.connection,''),json_extract(e.data,'$.connection'),json_extract(t.data,'$.source_connection'),'')
                         FROM activity AS a
                    LEFT JOIN records AS t ON t.bucket='tasks' AND t.id=a.task
                    LEFT JOIN records AS e ON e.bucket='events'
                          AND e.id=coalesce(nullif(json_extract(t.data,'$.event'),''),a.task)
                     ORDER BY a.seq DESC"""
                )
            ]
