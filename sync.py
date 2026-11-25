"""Pairing, sync and ownership between trusted instances of one workspace.

See docs/specs/instances-and-sync.md, "Phase 2 design". The protocol logic (log, merge, ownership,
handoffs) is independent of the network so tests can run several instances in one process.
"""

import base64
import hashlib
import hmac
import http.client
import http.server
import json
import os
import platform
import secrets
import shutil
import socket
import ssl
import subprocess
import threading
import time
import uuid
from pathlib import Path

PROTOCOL = 1
LOCAL_BUCKETS = {"local", "peers"}
LOCAL_FIELDS = {("config", "settings"): {"provider", "orchestrator_subscription", "slots", "preference"}}
OWNED = {"tasks": "task:", "connections": "connection:"}
ORCHESTRATOR = "orchestrator"
CODE_TTL = 600
CODE_ATTEMPTS = 5
HANDOFF_TIMEOUT = 600
EXCHANGE_SECONDS = 5
FRESH_SECONDS = 60
BATCH = 500
MAX_BODY = 16 * 1024 * 1024
DELETED = {"$deleted": True}
CONNECT_TIMEOUT = 5


class SyncError(RuntimeError):
    pass


# Sealing logins with the workspace key, standard library only (no AES in the Mac's Python).


def _prf(key, label, data=b""):
    return hmac.new(key, label + data, hashlib.sha256).digest()


def seal(key, identity, plaintext):
    nonce = os.urandom(16)
    data = plaintext.encode()
    enc, mac = _prf(key, b"enc"), _prf(key, b"mac")
    stream = b"".join(_prf(enc, nonce, i.to_bytes(4, "big")) for i in range((len(data) + 31) // 32))
    body = bytes(a ^ b for a, b in zip(data, stream))
    tag = _prf(mac, identity.encode() + b"\0" + nonce, body)
    return base64.b64encode(nonce + tag + body).decode()


def unseal(key, identity, sealed):
    raw = base64.b64decode(sealed)
    nonce, tag, body = raw[:16], raw[16:48], raw[48:]
    enc, mac = _prf(key, b"enc"), _prf(key, b"mac")
    if not hmac.compare_digest(tag, _prf(mac, identity.encode() + b"\0" + nonce, body)):
        raise SyncError("A login could not be verified with the workspace key.")
    stream = b"".join(_prf(enc, nonce, i.to_bytes(4, "big")) for i in range((len(body) + 31) // 32))
    return bytes(a ^ b for a, b in zip(body, stream)).decode()


# Identity: a self-signed certificate whose SHA-256 fingerprint names the instance.


def _openssl():
    for candidate in (shutil.which("openssl"), "/usr/bin/openssl", "/opt/homebrew/bin/openssl"):
        if candidate and os.path.exists(candidate):
            return candidate
    raise SyncError("openssl is required to create this instance's identity.")


def fingerprint(cert_pem):
    der = ssl.PEM_cert_to_DER_cert(cert_pem)
    return hashlib.sha256(der).hexdigest()


def make_identity(directory, instance_id):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    key, cert = directory / "key.pem", directory / "cert.pem"
    if not cert.exists():
        old = os.umask(0o077)
        try:
            subprocess.run(
                [_openssl(), "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                 "-nodes", "-keyout", str(key), "-out", str(cert), "-days", "36500",
                 "-subj", "/CN=memyselfai-" + instance_id,
                 "-addext", "basicConstraints=critical,CA:TRUE",
                 "-addext", "keyUsage=critical,digitalSignature,keyCertSign"],
                check=True, capture_output=True, timeout=30,
            )
        finally:
            os.umask(old)
    os.chmod(key, 0o600)
    return key, cert


class Clock:
    """Hybrid logical clock. Values sort as strings: wall milliseconds, counter, instance."""

    def __init__(self, instance, now):
        self.instance, self.now, self.wall, self.count = instance, now, 0, 0
        self.lock = threading.Lock()

    def tick(self):
        with self.lock:
            wall = int(self.now() * 1000)
            if wall > self.wall:
                self.wall, self.count = wall, 0
            else:
                self.count += 1
            return f"{self.wall:015d}.{self.count:06d}.{self.instance}"

    def observe(self, value):
        wall, count = int(value[:15]), int(value[16:22])
        with self.lock:
            if (wall, count) > (self.wall, self.count):
                self.wall, self.count = wall, count


class Sync:
    """One instance's view of the workspace log, its peers and item ownership."""

    def __init__(self, db, now=time.time, name=None, secrets_api=None, identity=True):
        self.db, self.now = db, now
        self.applying = threading.local()
        self.lock = threading.RLock()
        self.secrets = secrets_api
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.server = None
        self.thread = None
        self.on_change = None
        # Set by the engine: package_hook(item, to) builds what travels with an offer (a worktree bundle);
        # accept_hook(offer, package) prepares this instance and returns task fields, or raises to decline.
        self.package_hook = None
        self.accept_hook = None
        self.discarded = []
        c = db.conn
        with db.atomic():
            c.execute(
                "CREATE TABLE IF NOT EXISTS changes (seq INTEGER PRIMARY KEY, origin TEXT, origin_seq INTEGER,"
                " hlc TEXT, bucket TEXT, id TEXT, field TEXT, value TEXT, epoch INTEGER,"
                " UNIQUE(origin, origin_seq))"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS clocks (bucket TEXT, id TEXT, field TEXT, hlc TEXT,"
                " PRIMARY KEY(bucket, id, field))"
            )
            me = db.get("local", "instance")
            if not me:
                me = dict(id="instance", instance=uuid.uuid4().hex[:16], name=name or socket.gethostname().split(".")[0] or "Instance",
                          platform=platform.system().lower(), key_version=0, journal=False)
                db.put("local", me)
        self.id = me["instance"]
        self.clock = Clock(self.id, now)
        last = c.execute("SELECT max(hlc) FROM changes").fetchone()[0]
        if last:
            self.clock.observe(last)
        self.key_path = self.cert_path = None
        self.identity = identity
        self._fingerprint = None
        db.observer = self
        with db.atomic():
            self.claim_unowned()
            # Pairings made when the listener still asked for confirmation: the code already proved both ends.
            for p in db.rows("peers"):
                if not p.get("trusted") and not p.get("removed"):
                    db.put("peers", {**p, "trusted": True, "state": "paired"})

    # Local identity and peers

    @property
    def fingerprint(self):
        """Created on first use, so instances that never pair never need openssl."""
        if self._fingerprint is None:
            if self.identity:
                self.key_path, self.cert_path = make_identity(self.db.root / "instance", self.id)
                self._fingerprint = fingerprint(self.cert_path.read_text())
            else:
                self._fingerprint = hashlib.sha256(self.id.encode()).hexdigest()
        return self._fingerprint

    def cert(self):
        return self.cert_path.read_text() if self.fingerprint and self.cert_path else ""

    def me(self):
        return self.db.get("local", "instance")

    def _set_me(self, **fields):
        with self.db.atomic():
            self.db.put("local", {**self.me(), **fields})

    def peers(self):
        return [p for p in self.db.rows("peers") if not p.get("removed")]

    def peer(self, key):
        p = self.db.get("peers", key)
        return p if p and not p.get("removed") else None

    def paired(self):
        return bool(self.me().get("journal"))

    def key_rank(self, me=None):
        me = me or self.me()
        key = me.get("workspace_key", "")
        return [me.get("key_version", 0), hashlib.sha256(key.encode()).hexdigest()[:16] if key else ""]

    def workspace_key(self):
        me = self.me()
        return bytes.fromhex(me["workspace_key"]) if me.get("workspace_key") else None

    # Ownership

    def owner(self, item):
        return self.db.get("owners", item)

    def pending_offer(self, item, record=None):
        record = record or self.owner(item)
        if not record:
            return None
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT data FROM records WHERE bucket='handoffs' AND json_extract(data,'$.item')=?"
                " AND json_extract(data,'$.epoch')=?", (item, record["epoch"] + 1)).fetchall()
        for (data,) in rows:
            offer = json.loads(data)
            if "from" in offer and offer.get("answer") not in {"cancel_ack", "declined"}:
                return offer
        return None

    def owns(self, item):
        record = self.owner(item)
        return bool(record and record["owner"] == self.id and not self.pending_offer(item, record))

    def epoch(self, bucket, key):
        prefix = OWNED.get(bucket)
        record = self.owner(prefix + key) if prefix else None
        return record["epoch"] if record else 0

    def claim(self, item, epoch=1):
        self.db.put("owners", dict(id=item, owner=self.id, epoch=epoch, at=self.now()))

    def claim_unowned(self):
        for bucket, prefix in OWNED.items():
            for row in self.db.rows(bucket):
                if not self.owner(prefix + row["id"]):
                    self.claim(prefix + row["id"])
        if not self.owner(ORCHESTRATOR):
            self.claim(ORCHESTRATOR)

    def owner_name(self, item):
        record = self.owner(item)
        if not record:
            return ""
        if record["owner"] == self.id:
            return self.me()["name"]
        peer = self.db.get("peers", record["owner"])
        return peer["name"] if peer else record["owner"][:8]

    def request_move(self, item, to):
        """Ask the owner, wherever it is, to hand the item to an instance."""
        if to != self.id and not self.peer(to):
            raise ValueError("Unknown instance.")
        record = self.owner(item)
        if not record:
            raise ValueError("Unknown item.")
        if record["owner"] == to:
            return "already"
        with self.db.atomic():
            self.db.put("handoff_requests", dict(id=item, to=to, at=self.now(), by=self.id))
        self.wake.set()
        return "requested"

    def offer(self, item, to, idle=lambda item: True):
        """Offer an idle item to a peer. A package (such as a worktree bundle) travels with the offer."""
        record = self.owner(item)
        if not record or record["owner"] != self.id or self.pending_offer(item, record) or to == self.id:
            return False
        if not idle(item):
            return False
        package = None
        if self.package_hook:
            try:
                package = self.package_hook(item, to)
            except Exception as exc:
                self.db.log("Handoff not offered", f"{item}: {exc}")
                return False
        with self.db.atomic():
            record = self.owner(item)
            if not record or record["owner"] != self.id or self.pending_offer(item, record) or not idle(item):
                return False
            epoch = record["epoch"] + 1
            offer_id = f"{item}@{epoch}:{uuid.uuid4().hex[:8]}"
            if package:
                self.db.put("bundles", {"id": offer_id, "item": item, **package})
            self.db.put("handoffs", {"id": offer_id, "item": item, "epoch": epoch, "from": self.id,
                                     "to": to, "at": self.now(), "cancelled": False, "answer": "",
                                     "package": bool(package)})
        self.wake.set()
        return True

    def review(self, idle=lambda item: True):
        """Advance handoffs: answer offers to this instance, time out its own offers, honour requests."""
        changed = False
        incoming = []
        with self.db.atomic():
            for offer in self.db.rows("handoffs"):
                if offer.get("answer") or not {"from", "to", "item", "epoch"} <= offer.keys():
                    continue
                if offer["to"] == self.id:
                    if offer.get("cancelled"):
                        offer["answer"] = "cancel_ack"
                        self.db.put("handoffs", offer)
                        changed = True
                    elif self._current(offer) and (not offer.get("package") or self.db.get("bundles", offer["id"])):
                        incoming.append(offer)
                elif offer["from"] == self.id and not offer.get("cancelled"):
                    record = self.owner(offer["item"])
                    current = record and record["epoch"] == offer["epoch"] - 1
                    if current and self.now() - offer["at"] > HANDOFF_TIMEOUT:
                        offer["cancelled"] = True
                        self.db.put("handoffs", offer)
                        changed = True
            requests = []
            for request in self.db.rows("handoff_requests"):
                if request.get("done"):
                    continue
                record = self.owner(request["id"])
                if record and record["owner"] == request["to"]:
                    self.db.put("handoff_requests", {**request, "done": True})
                    changed = True
                elif record and record["owner"] == self.id:
                    requests.append(request)
        for offer in incoming:
            # Accepting may import a worktree, so it runs outside the database transaction.
            accepted = {}
            if self.accept_hook:
                try:
                    accepted = self.accept_hook(offer, self.db.get("bundles", offer["id"]))
                except Exception as exc:
                    accepted = None
                    reason = str(exc)
            with self.db.atomic():
                latest = self.db.get("handoffs", offer["id"])
                if not latest or latest.get("answer") or not self._current(latest):
                    continue
                if latest.get("cancelled"):
                    latest["answer"] = "cancel_ack"
                elif accepted is None:
                    latest.update(answer="declined", reason=reason[:300])
                    self.db.log("Handoff declined", f"{offer['item']}: {reason}")
                else:
                    self.db.put("owners", dict(id=offer["item"], owner=self.id, epoch=offer["epoch"], at=self.now()))
                    prefix, _, key = offer["item"].partition(":")
                    if accepted and prefix == "task" and self.db.get("tasks", key):
                        self.db.put("tasks", {**self.db.get("tasks", key), **accepted})
                    if self.db.get("bundles", offer["id"]):
                        self.db.delete("bundles", offer["id"])
                    latest["answer"] = "confirmed"
                self.db.put("handoffs", latest)
                changed = True
        for request in requests:
            if self.offer(request["id"], request["to"], idle):
                with self.db.atomic():
                    self.db.put("handoff_requests", {**request, "done": True})
                changed = True
        return changed

    def declined_recently(self, item, to, seconds=3600):
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT data FROM records WHERE bucket='handoffs' AND json_extract(data,'$.item')=?"
                " AND json_extract(data,'$.to')=? AND json_extract(data,'$.answer')='declined'", (item, to)).fetchall()
        return any(self.now() - json.loads(d)["at"] < seconds for (d,) in rows)

    def _current(self, offer):
        record = self.owner(offer["item"])
        return bool(record and record["epoch"] == offer["epoch"] - 1 and record["owner"] == offer["from"])

    def takeover(self, peer_id, items=None):
        """User-confirmed: own the items of an instance that cannot hand them over. Returns the items."""
        taken = []
        with self.db.atomic():
            for record in self.db.rows("owners"):
                if record["owner"] != peer_id or (items is not None and record["id"] not in items):
                    continue
                self.db.put("owners", dict(id=record["id"], owner=self.id, epoch=record["epoch"] + 2,
                                           at=self.now(), taken_from=peer_id))
                taken.append(record["id"])
        self.wake.set()
        return taken

    # The log

    def record(self, bucket, key, old, new):
        """Store observer: journal a local write. Runs inside the Store's lock."""
        if getattr(self.applying, "on", False) or bucket in LOCAL_BUCKETS:
            return
        if old is None and new is not None and bucket in OWNED and not self.owner(OWNED[bucket] + key):
            self.claim(OWNED[bucket] + key)
        if not self.paired():
            return
        epoch = self.epoch(bucket, key)
        if bucket == "owners":
            self._append(bucket, key, "*", new, (new or {}).get("epoch", 0))
            return
        if new is None:
            self._append(bucket, key, "", None, epoch)
            return
        skip = LOCAL_FIELDS.get((bucket, key), ())
        if not old:
            # A new row travels as one entry, so a peer never sees it half built.
            self._append(bucket, key, "*", {k: v for k, v in new.items() if k not in skip}, epoch)
            return
        for field in sorted(set(old) | set(new)):
            if field in skip or field == "id":
                continue
            if field not in new:
                self._append(bucket, key, field, DELETED, epoch)
            elif field not in old or old[field] != new[field]:
                self._append(bucket, key, field, new[field], epoch)

    def record_activity(self, row):
        if getattr(self.applying, "on", False) or not self.paired():
            return
        self._append("@activity", "", "", row, 0)

    def record_secret(self, identity, secret):
        if getattr(self.applying, "on", False) or not self.paired():
            return
        key = self.workspace_key()
        if not key:
            return
        value = None if secret is None else seal(key, identity, secret)
        with self.db.atomic():
            self._append("@secret", identity, "", value, 0)

    def _append(self, bucket, key, field, value, epoch):
        c = self.db.conn
        hlc = self.clock.tick()
        origin_seq = (c.execute("SELECT max(origin_seq) FROM changes WHERE origin=?", (self.id,)).fetchone()[0]
                      or 0) + 1
        c.execute(
            "INSERT INTO changes(origin,origin_seq,hlc,bucket,id,field,value,epoch) VALUES (?,?,?,?,?,?,?,?)",
            (self.id, origin_seq, hlc, bucket, key, field, json.dumps(value), epoch),
        )
        if bucket[0] != "@" and bucket != "owners":
            for name in (value if field == "*" else {field: None}):
                c.execute("INSERT INTO clocks VALUES (?,?,?,?) ON CONFLICT DO UPDATE SET hlc=excluded.hlc",
                          (bucket, key, name, hlc))
        if self.on_change:
            self.on_change()

    def start_journal(self):
        """First peer: write every record and login to the log as a baseline."""
        if self.paired():
            return
        with self.db.atomic():
            if not self.me().get("workspace_key"):
                self._set_me(workspace_key=secrets.token_hex(32), key_version=1)
            self._set_me(journal=True)
            for (bucket,) in self.db.conn.execute("SELECT DISTINCT bucket FROM records").fetchall():
                if bucket in LOCAL_BUCKETS:
                    continue
                for row in self.db.rows(bucket):
                    self.record(bucket, row["id"], None, row)
            for row in self.db.conn.execute(
                "SELECT at,kind,project,task,detail,connection FROM activity ORDER BY seq"
            ).fetchall():
                self._append("@activity", "", "", dict(zip(("at", "kind", "project", "task", "detail",
                                                            "connection"), row)), 0)
        self.publish_secrets()

    def publish_secrets(self):
        if not self.secrets:
            return
        for identity in self.secrets.identities(self.db):
            try:
                value = self.secrets.read(identity)
            except Exception:
                continue
            self.record_secret(identity, value)

    def entries(self, since, limit=BATCH):
        rows = self.db.conn.execute(
            "SELECT seq,origin,origin_seq,hlc,bucket,id,field,value,epoch FROM changes WHERE seq>? ORDER BY seq"
            " LIMIT ?", (since, limit)).fetchall()
        cols = ("seq", "origin", "origin_seq", "hlc", "bucket", "id", "field", "value", "epoch")
        return [dict(zip(cols, r)) for r in rows]

    def head(self):
        return self.db.conn.execute("SELECT coalesce(max(seq),0) FROM changes").fetchone()[0]

    def apply(self, entries):
        """Apply a peer's log entries. Returns how many were new."""
        applied = 0
        with self.db.atomic():
            self.applying.on = True
            try:
                for ch in entries:
                    if ch["origin"] == self.id:
                        continue
                    seen = self.db.conn.execute("SELECT 1 FROM changes WHERE origin=? AND origin_seq=?",
                                                (ch["origin"], ch["origin_seq"])).fetchone()
                    if seen:
                        continue
                    self.db.conn.execute(
                        "INSERT INTO changes(origin,origin_seq,hlc,bucket,id,field,value,epoch)"
                        " VALUES (?,?,?,?,?,?,?,?)",
                        (ch["origin"], ch["origin_seq"], ch["hlc"], ch["bucket"], ch["id"], ch["field"],
                         ch["value"], ch["epoch"]))
                    self.clock.observe(ch["hlc"])
                    self._apply_one(ch, json.loads(ch["value"]))
                    applied += 1
            finally:
                self.applying.on = False
        if self.discarded:
            # Kept on the task by its new owner, so the user can see what the old owner had done.
            with self.db.atomic():
                for key, attempt in self.discarded:
                    t = self.db.get("tasks", key)
                    if t:
                        self.db.put("tasks", {**t, "discarded": (t.get("discarded") or [])[-19:] + [attempt]})
            self.discarded = []
        if applied:
            self.apply_revocations()
        return applied

    def _apply_one(self, ch, value):
        bucket, key, field = ch["bucket"], ch["id"], ch["field"]
        if bucket in LOCAL_BUCKETS:
            return
        if bucket == "@activity":
            self.db.conn.execute(
                "INSERT INTO activity(at,kind,project,task,detail,connection) VALUES (?,?,?,?,?,?)",
                tuple(str(value.get(k, "")) if k != "at" else value.get("at", 0)
                      for k in ("at", "kind", "project", "task", "detail", "connection")))
            return
        if bucket == "@secret":
            self._apply_secret(key, value)
            return
        if bucket == "owners":
            self._apply_owner(value)
            return
        if bucket in OWNED and ch["epoch"] < self.epoch(bucket, key):
            self.db.log("Refused change from an older owner", f"{bucket} {key} field {field}")
            if bucket == "tasks" and self.owns("task:" + key):
                self.discarded.append((key, dict(field=field, value=value, instance=ch["origin"],
                                                 epoch=ch["epoch"], at=self.now())))
            return
        if field == "*":
            fields = {name: v for name, v in value.items()
                      if name != "id" and self._newer(bucket, key, name, ch["hlc"])}
            row = dict(self.db.get(bucket, key) or {"id": key})
            row.update(fields)
            self.db.put(bucket, row)
            return
        if not self._newer(bucket, key, field, ch["hlc"]):
            return
        row = self.db.get(bucket, key)
        if field == "":
            newest = self.db.conn.execute("SELECT max(hlc) FROM clocks WHERE bucket=? AND id=? AND field!=''",
                                          (bucket, key)).fetchone()[0]
            if row is not None and (not newest or newest <= ch["hlc"]):
                self.db.delete(bucket, key)
            return
        row = dict(row or {"id": key})
        if value == DELETED:
            row.pop(field, None)
        elif field != "id":
            row[field] = value
        self.db.put(bucket, row)

    def _newer(self, bucket, key, field, hlc):
        """Last writer wins per field. Records the clock when the change wins."""
        clock = self.db.conn.execute("SELECT hlc FROM clocks WHERE bucket=? AND id=? AND field=?",
                                     (bucket, key, field)).fetchone()
        if clock and clock[0] >= hlc:
            return False
        self.db.conn.execute("INSERT INTO clocks VALUES (?,?,?,?) ON CONFLICT DO UPDATE SET hlc=excluded.hlc",
                             (bucket, key, field, hlc))
        return True

    def _apply_owner(self, value):
        if not value:
            return
        current = self.owner(value["id"])
        if current and current["epoch"] > value["epoch"]:
            return
        if current and current["epoch"] == value["epoch"]:
            if current["owner"] == value["owner"]:
                return
            winner = min(current["owner"], value["owner"])
            self.db.log("Ownership conflict resolved", f"{value['id']}: kept {winner} at epoch {value['epoch']}")
            if winner == current["owner"]:
                return
        self.db.put("owners", value)

    def _apply_secret(self, identity, value):
        if not self.secrets:
            return
        if value is None:
            try:
                self.secrets.delete(identity)
            except Exception:
                pass
            return
        key = self.workspace_key()
        try:
            self.secrets.store(identity, unseal(key, identity, value))
        except Exception as exc:
            self.db.log("Login not applied", f"{identity}: {exc}")

    # Exchanges. The caller sends its new entries and receives the peer's.

    def heartbeat(self, capacity=None):
        me = self.me()
        return dict(id=self.id, name=me["name"], platform=me["platform"], fingerprint=self.fingerprint,
                    protocol=PROTOCOL, at=self.now(), capacity=capacity or {})

    def exchange_request(self, peer_id, capacity=None):
        p = self.peer(peer_id)
        entries = self.entries(p.get("acked", 0))
        rank = self.key_rank()
        request = dict(instance=self.heartbeat(capacity), since=p.get("cursor", 0), entries=entries, key_rank=rank)
        if p.get("key_rank", [0, ""]) < rank:
            request["workspace_key"] = self.me()["workspace_key"]
        return request

    def handle_exchange(self, peer_id, request, capacity=None):
        p = self.peer(peer_id)
        if not p or not p.get("trusted"):
            raise PermissionError("awaiting confirmation" if p else "not paired")
        if request.get("protocol", request["instance"].get("protocol")) != PROTOCOL:
            raise SyncError("Update both instances to the same version to sync.")
        self.start_journal()
        self._take_key(request)
        self.apply(request["entries"])
        acked = max([e["seq"] for e in request["entries"]], default=0)
        with self.db.atomic():
            p = self.db.get("peers", peer_id)
            p.update(last_seen=self.now(), info=request["instance"], state="connected", error="",
                     key_rank=request.get("key_rank", [0, ""]))
            self.db.put("peers", p)
        rank = self.key_rank()
        response = dict(instance=self.heartbeat(capacity), acked=acked, entries=self.entries(request["since"]),
                        head=self.head(), key_rank=rank)
        if request.get("key_rank", [0, ""]) < rank:
            response["workspace_key"] = self.me()["workspace_key"]
        return response

    def finish_exchange(self, peer_id, request, response):
        self._take_key(response)
        self.apply(response["entries"])
        with self.db.atomic():
            p = self.db.get("peers", peer_id)
            cursor = max([e["seq"] for e in response["entries"]], default=p.get("cursor", 0))
            sent = max([e["seq"] for e in request["entries"]], default=p.get("acked", 0))
            p.update(last_seen=self.now(), info=response["instance"], cursor=cursor,
                     acked=max(p.get("acked", 0), min(sent, response["acked"])), state="connected", error="",
                     key_rank=response.get("key_rank", [0, ""]))
            self.db.put("peers", p)
        return cursor < response.get("head", cursor) or self.head() > p["acked"]

    def _take_key(self, message):
        """The higher-ranked workspace key wins. Re-seal this instance's logins with it."""
        key, rank = message.get("workspace_key"), message.get("key_rank", [0, ""])
        if not key or rank <= self.key_rank():
            return
        if self.key_rank({"workspace_key": key, "key_version": rank[0]}) != rank:
            raise SyncError("The workspace key does not match its fingerprint.")
        self._set_me(workspace_key=key, key_version=rank[0])
        self.publish_secrets()

    def sync_with(self, peer_id, send, capacity=None, rounds=50):
        """Run exchanges with one peer until both are caught up. send(request) returns the response."""
        self.start_journal()
        for _ in range(rounds):
            request = self.exchange_request(peer_id, capacity)
            response = send(request)
            if not self.finish_exchange(peer_id, request, response):
                return

    # Pairing

    def create_code(self):
        code = base64.b32encode(os.urandom(15)).decode()
        with self.db.atomic():
            self.db.put("local", dict(id="pairing", code=code, expires=self.now() + CODE_TTL, attempts=0))
        self.start_journal()
        return "-".join(code[i:i + 4] for i in range(0, 24, 4))

    @staticmethod
    def _norm(code):
        return "".join(ch for ch in code.upper() if ch.isalnum())

    @staticmethod
    def proof(code, role, first, second, nonce):
        return hmac.new(Sync._norm(code).encode(), f"{role}|{first}|{second}|{nonce}".encode(),
                        hashlib.sha256).hexdigest()

    def handle_pair(self, request, own_fp=None):
        own_fp = own_fp or self.fingerprint
        with self.db.atomic():
            pending = self.db.get("local", "pairing")
            if not pending or pending["expires"] < self.now() or pending["attempts"] >= CODE_ATTEMPTS:
                raise PermissionError("The pairing code has expired. Create a new one.")
            cert = request["cert"]
            their_fp = fingerprint(cert) if "BEGIN CERTIFICATE" in cert else request["instance"]["fingerprint"]
            match = pending["code"]
            expected = self.proof(match, "join", their_fp, own_fp, request["nonce"])
            if not hmac.compare_digest(expected, str(request.get("proof", ""))):
                pending["attempts"] += 1
                self.db.put("local", pending)
                raise PermissionError("The pairing code is wrong.")
            self.db.delete("local", "pairing")
            info = request["instance"]
            self.db.put("peers", dict(id=info["id"], name=info["name"], platform=info["platform"],
                                      cert=cert, fingerprint=their_fp, trusted=True, address="",
                                      state="paired", paired_at=self.now()))
        self.reload_server()
        return dict(instance=self.heartbeat(), cert=self.cert(),
                    proof=self.proof(match, "host", own_fp, their_fp, request["nonce"]))

    def pair_request(self, code, host_fp):
        nonce = secrets.token_hex(16)
        me = self.heartbeat()
        cert = self.cert()
        return nonce, dict(instance=me, cert=cert, nonce=nonce,
                           proof=self.proof(code, "join", self.fingerprint, host_fp, nonce))

    def finish_pair(self, code, host_fp, nonce, response, address):
        cert = response["cert"]
        fp = fingerprint(cert) if "BEGIN CERTIFICATE" in cert else response["instance"]["fingerprint"]
        expected = self.proof(code, "host", host_fp, self.fingerprint, nonce)
        if fp != host_fp or not hmac.compare_digest(expected, response["proof"]):
            raise SyncError("The other instance could not prove it holds this code. Pairing stopped.")
        info = response["instance"]
        with self.db.atomic():
            self.db.put("peers", dict(id=info["id"], name=info["name"], platform=info["platform"], cert=cert,
                                      fingerprint=fp, trusted=True, address=address,
                                      state="paired", paired_at=self.now()))
        self.start_journal()
        return info

    def remove(self, peer_id):
        with self.db.atomic():
            p = self.db.get("peers", peer_id)
            if not p:
                raise ValueError("Unknown instance.")
            p.update(removed=True, trusted=False, state="removed")
            self.db.put("peers", p)
            self.db.put("revoked", dict(id=peer_id, fingerprint=p.get("fingerprint", ""), at=self.now()))
            self._set_me(workspace_key=secrets.token_hex(32), key_version=self.me().get("key_version", 0) + 1)
            for c in self.db.rows("connections"):
                self.db.put("connections", {**c, "reconnect_to_revoke": p["name"]})
        self.publish_secrets()
        self.reload_server()

    def apply_revocations(self):
        for revoked in self.db.rows("revoked"):
            p = self.db.get("peers", revoked["id"])
            if revoked["id"] == self.id:
                continue
            if p and not p.get("removed"):
                with self.db.atomic():
                    self.db.put("peers", {**p, "removed": True, "trusted": False, "state": "removed"})
                self.reload_server()

    def rename(self, name):
        name = " ".join(str(name).split())[:60]
        if not name:
            raise ValueError("Enter a name.")
        self._set_me(name=name)

    # Network: mutual TLS listener and client

    def _server_context(self):
        assert self.fingerprint
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert_path, self.key_path)
        ctx.verify_mode = ssl.CERT_OPTIONAL
        trusted = "".join(p["cert"] for p in self.peers() if p.get("cert"))
        if trusted:
            ctx.load_verify_locations(cadata=trusted)
        return ctx

    def _client_context(self, peer=None):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        if peer:
            assert self.fingerprint
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.load_verify_locations(cadata=peer["cert"])
            ctx.load_cert_chain(self.cert_path, self.key_path)
        else:
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def reload_server(self):
        if self.server:
            self.server.context = self._server_context()

    def listen(self, bind="0.0.0.0", port=8802, capacity=lambda: {}):
        sync = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    der = self.connection.getpeercert(binary_form=True)
                    caller = hashlib.sha256(der).hexdigest() if der else None
                    length = int(self.headers.get("Content-Length", "0"))
                    # Only a verified peer certificate may send large bodies; pairing requests are small.
                    if length > (MAX_BODY if caller else 64 * 1024) or length < 0:
                        raise ValueError("Request too large.")
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if self.path == "/pair":
                        result = sync.handle_pair(body)
                    elif self.path == "/exchange":
                        peer = next((p for p in sync.peers() if caller and p.get("fingerprint") == caller), None)
                        if not peer:
                            raise PermissionError("This instance is not paired.")
                        result = sync.handle_exchange(peer["id"], body, capacity())
                        sync.wake.set()  # answer handoff offers promptly
                    else:
                        raise FileNotFoundError(self.path)
                    status = 200
                except PermissionError as exc:
                    status, result = 403, {"error": str(exc)}
                except FileNotFoundError:
                    status, result = 404, {"error": "Not found."}
                except Exception as exc:
                    status, result = 400, {"error": str(exc)}
                data = json.dumps(result).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        class Server(http.server.ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass  # failed handshakes from unpaired clients are expected

            def get_request(self):
                sock, addr = self.socket.accept()
                sock.settimeout(30)
                return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

        self.server = Server((bind, port), Handler)
        self.server.context = self._server_context()
        threading.Thread(target=self.server.serve_forever, daemon=True, name="sync-listener").start()
        return self.server.server_address[1]

    @staticmethod
    def _split(address):
        host, _, port = address.strip().rpartition(":")
        if not host or not port.isdigit():
            raise ValueError("Enter the address as host:port, for example vps.example.com:8802.")
        return host.strip("[]"), int(port)

    def _post(self, address, path, body, peer=None):
        host, port = self._split(address)
        # A short connect timeout keeps an offline peer from stalling sync and quitting for 30 seconds.
        conn = http.client.HTTPSConnection(host, port, timeout=CONNECT_TIMEOUT, context=self._client_context(peer))
        try:
            conn.connect()
            conn.sock.settimeout(30)
            der = conn.sock.getpeercert(binary_form=True)
            server_fp = hashlib.sha256(der).hexdigest()
            if peer and server_fp != peer["fingerprint"]:
                raise SyncError("The peer presented a different certificate.")
            data = json.dumps(body).encode()
            conn.request("POST", path, data, {"Content-Type": "application/json"})
            reply = conn.getresponse()
            result = json.loads(reply.read() or b"{}")
            if reply.status == 403:
                raise PermissionError(result.get("error", "Refused"))
            if reply.status != 200:
                raise SyncError(result.get("error", f"HTTP {reply.status}"))
            return server_fp, result
        finally:
            conn.close()

    def join(self, address, code):
        host_fp, _ = None, None
        host, port = self._split(address)
        probe = self._client_context().wrap_socket(socket.create_connection((host, port), timeout=15))
        try:
            host_fp = hashlib.sha256(probe.getpeercert(binary_form=True)).hexdigest()
        finally:
            probe.close()
        nonce, request = self.pair_request(code, host_fp)
        server_fp, response = self._post(address, "/pair", request)
        if server_fp != host_fp:
            raise SyncError("The other instance changed its certificate during pairing.")
        info = self.finish_pair(code, host_fp, nonce, response, address)
        self.wake.set()
        return info

    def exchange_all(self, capacity=lambda: {}):
        for p in self.peers():
            if not p.get("address") or not p.get("trusted"):
                continue
            try:
                self.sync_with(p["id"], lambda req, p=p: self._post(p["address"], "/exchange", req, p)[1],
                               capacity())
            except PermissionError as exc:
                with self.db.atomic():
                    self.db.put("peers", {**self.db.get("peers", p["id"]), "state": str(exc), "error": str(exc)})
            except Exception as exc:
                with self.db.atomic():
                    self.db.put("peers", {**self.db.get("peers", p["id"]), "state": "unreachable",
                                          "error": str(exc)[:300]})

    def run(self, capacity=lambda: {}, idle=lambda item: True, changed=lambda: None):
        while not self.stop.is_set():
            try:
                before = self.head()
                self.review(idle)
                self.exchange_all(capacity)
                self.review(idle)
                if self.head() != before:
                    changed()
            except Exception as exc:
                self.db.log("Sync error", str(exc))
            self.wake.wait(EXCHANGE_SECONDS)
            self.wake.clear()

    def start(self, **kwargs):
        self.thread = threading.Thread(target=self.run, kwargs=kwargs, daemon=True, name="sync")
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(5)

    def state(self):
        me = self.me()
        owned = {}
        for record in self.db.rows("owners"):
            owned.setdefault(record["owner"], []).append(record["id"])
        pending = self.db.get("local", "pairing")
        peers = []
        for p in self.db.rows("peers"):
            if p.get("removed"):
                continue
            info = p.get("info") or {}
            peers.append(dict(id=p["id"], name=p["name"], platform=p["platform"], fingerprint=p["fingerprint"],
                              trusted=p.get("trusted", False), address=p.get("address", ""),
                              state=p.get("state", ""), last_seen=p.get("last_seen"),
                              capacity=info.get("capacity", {}), owns=len(owned.get(p["id"], []))))
        departed = [dict(id=p["id"], name=p["name"], owns=len(owned.get(p["id"], [])))
                    for p in self.db.rows("peers") if p.get("removed") and owned.get(p["id"])]
        local = self.db.get("local", "checkouts") or {}
        return dict(id=self.id, name=me["name"], platform=me["platform"], fingerprint=self.fingerprint,
                    departed=departed, checkouts=local.get("paths", []),
                    listening=self.server.server_address[1] if self.server else None,
                    paired=self.paired(), owns=len(owned.get(self.id, [])), peers=peers,
                    code_active=bool(pending and pending["expires"] > self.now()),
                    orchestrator=self.owner_name(ORCHESTRATOR))
