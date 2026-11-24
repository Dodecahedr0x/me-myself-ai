"""Pairing, the synced log and the ownership protocol. See docs/specs/instances-and-sync.md, phase 2."""

import json
import os
import random
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import connectors
import sync
import worker
from engine import Engine
from store import Store


class FakeClock:
    def __init__(self, start=1_800_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class MemorySecrets:
    def __init__(self):
        self.values = {}

    def identities(self, db):
        return list(self.values)

    def read(self, identity):
        return self.values[identity]

    def store(self, identity, secret):
        self.values[identity] = secret

    def delete(self, identity):
        self.values.pop(identity, None)


class Node:
    """One instance on a plain Store, without the network."""

    def __init__(self, root, clock, name):
        self.root, self.clock, self.name = root, clock, name
        self.secrets = MemorySecrets()
        self.open()

    def open(self):
        self.db = Store(self.root)
        self.sync = sync.Sync(self.db, self.clock, name=self.name, secrets_api=self.secrets, identity=False)

    def crash(self):
        self.db.conn.close()
        self.open()

    @property
    def id(self):
        return self.sync.id


def trust(*nodes):
    for a in nodes:
        for b in nodes:
            if a is not b:
                with a.db.atomic():
                    a.db.put("peers", dict(id=b.id, name=b.name, platform="test", cert="", fingerprint=b.id,
                                           trusted=True, address="memory"))
        a.sync.start_journal()


def wire(message):
    return json.loads(json.dumps(message))


def exchange(a, b):
    a.sync.sync_with(b.id, lambda request: wire(b.sync.handle_exchange(a.id, wire(request))))


def settle(nodes, rounds=4):
    for _ in range(rounds):
        for a in nodes:
            for b in nodes:
                if a is not b:
                    exchange(a, b)
            a.sync.review()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()

    def node(self, name):
        n = Node(Path(self.tmp.name) / name, self.clock, name)
        self.addCleanup(lambda: n.db.conn.close())
        return n


class SealTests(unittest.TestCase):
    def test_round_trip_and_tamper(self):
        key = bytes(range(32))
        sealed = sync.seal(key, "conn-1", "xoxp-fixture-token")
        self.assertNotIn("xoxp", sealed)
        self.assertEqual(sync.unseal(key, "conn-1", sealed), "xoxp-fixture-token")
        with self.assertRaises(sync.SyncError):
            sync.unseal(key, "conn-2", sealed)
        with self.assertRaises(sync.SyncError):
            sync.unseal(bytes(32), "conn-1", sealed)


class LogTests(Base):
    def test_records_and_activity_reach_every_peer_through_any_path(self):
        a, b, c = self.node("a"), self.node("b"), self.node("c")
        with a.db.atomic():
            a.db.put("tasks", dict(id="t1", title="From the laptop", status="queued"))
            a.db.log("queued", "Task created", task="t1")
        trust(a, b)
        trust(b, c)
        exchange(a, b)
        exchange(c, b)
        self.assertEqual(c.db.get("tasks", "t1")["title"], "From the laptop")
        self.assertEqual(c.sync.owner("task:t1")["owner"], a.id)
        self.assertTrue(any(row["task"] == "t1" for row in c.db.history()))
        # Applying again changes nothing.
        before = c.sync.head()
        exchange(c, b)
        self.assertEqual(c.sync.head(), before)

    def test_edits_while_cut_off_converge_to_the_later_one(self):
        a, b = self.node("a"), self.node("b")
        trust(a, b)
        with a.db.atomic():
            a.db.put("projects", dict(id="p", name="Home", note="x"))
        exchange(a, b)
        with a.db.atomic():
            a.db.put("projects", dict(id="p", name="Home on laptop", note="x"))
        self.clock.advance(1)
        with b.db.atomic():
            b.db.put("projects", dict(id="p", name="Home on VPS", note="y"))
        exchange(a, b)
        exchange(b, a)
        self.assertEqual(a.db.get("projects", "p"), b.db.get("projects", "p"))
        self.assertEqual(a.db.get("projects", "p")["name"], "Home on VPS")

    def test_local_settings_stay_local(self):
        a, b = self.node("a"), self.node("b")
        with a.db.atomic():
            a.db.put("config", dict(id="settings", dry=True, provider="codex"))
        with b.db.atomic():
            b.db.put("config", dict(id="settings", dry=True, provider="claude"))
        trust(a, b)
        with a.db.atomic():
            a.db.put("config", dict(id="settings", dry=False, provider="codex"))
        exchange(a, b)
        self.assertEqual(b.db.get("config", "settings"), dict(id="settings", dry=False, provider="claude"))

    def test_logins_travel_sealed_and_follow_key_rotation(self):
        a, b, c = self.node("a"), self.node("b"), self.node("c")
        a.secrets.values["conn-1"] = "xoxp-fixture-token"
        trust(a, b)
        captured = []

        def send(request):
            captured.append(json.dumps(request))
            response = wire(b.sync.handle_exchange(a.id, wire(request)))
            captured.append(json.dumps(response))
            return response

        a.sync.sync_with(b.id, send)
        self.assertEqual(b.secrets.values.get("conn-1"), "xoxp-fixture-token")
        self.assertFalse(any("xoxp-fixture-token" in body for body in captured))
        # Both had generated a key; they agree on one.
        self.assertEqual(a.sync.workspace_key(), b.sync.workspace_key())
        # Removing a peer rotates the key; the remaining peer still receives new logins.
        trust(a, c)
        trust(b, c)
        exchange(a, c)
        exchange(a, b)
        old = a.sync.workspace_key()
        a.sync.remove(c.id)
        self.assertNotEqual(a.sync.workspace_key(), old)
        a.secrets.values["conn-2"] = "second-token"
        a.sync.record_secret("conn-2", "second-token")
        exchange(a, b)
        self.assertEqual(b.sync.workspace_key(), a.sync.workspace_key())
        self.assertEqual(b.secrets.values.get("conn-2"), "second-token")
        self.assertTrue(b.db.get("peers", c.id).get("removed"))
        with self.assertRaises(PermissionError):
            a.sync.handle_exchange(c.id, wire(c.sync.exchange_request(a.id)))


class OwnershipTests(Base):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.node("a"), self.node("b")
        with self.a.db.atomic():
            self.a.db.put("tasks", dict(id="t1", title="Reply", status="queued"))
        trust(self.a, self.b)
        exchange(self.a, self.b)

    def test_creator_owns_and_handoff_moves_ownership_once(self):
        a, b = self.a, self.b
        self.assertTrue(a.sync.owns("task:t1"))
        self.assertFalse(b.sync.owns("task:t1"))
        self.assertTrue(a.sync.offer("task:t1", b.id))
        self.assertFalse(a.sync.owns("task:t1"), "the owner stops acting once it offers")
        exchange(a, b)
        b.sync.review()
        self.assertTrue(b.sync.owns("task:t1"))
        self.assertFalse(a.sync.owns("task:t1"))
        exchange(a, b)
        self.assertFalse(a.sync.owns("task:t1"))
        self.assertEqual(a.sync.owner("task:t1"), b.sync.owner("task:t1"))
        self.assertEqual(a.sync.owner("task:t1")["epoch"], 2)

    def test_unanswered_offer_is_cancelled_only_with_the_receivers_ack(self):
        a, b = self.a, self.b
        a.sync.offer("task:t1", b.id)
        self.clock.advance(sync.HANDOFF_TIMEOUT + 1)
        a.sync.review()
        self.assertFalse(a.sync.owns("task:t1"), "cancelled but not acknowledged")
        exchange(a, b)
        b.sync.review()
        self.assertFalse(b.sync.owns("task:t1"))
        exchange(a, b)
        self.assertTrue(a.sync.owns("task:t1"))
        # A new offer at the same epoch is a separate record and works.
        self.assertTrue(a.sync.offer("task:t1", b.id))
        exchange(a, b)
        b.sync.review()
        self.assertTrue(b.sync.owns("task:t1"))

    def test_cancel_racing_a_confirmation_leaves_one_owner(self):
        a, b = self.a, self.b
        a.sync.offer("task:t1", b.id)
        exchange(a, b)
        b.sync.review()  # b confirms and owns
        self.clock.advance(sync.HANDOFF_TIMEOUT + 1)
        a.sync.review()  # a has not heard yet and cancels
        exchange(a, b)
        b.sync.review()
        exchange(a, b)
        self.assertTrue(b.sync.owns("task:t1"))
        self.assertFalse(a.sync.owns("task:t1"))

    def test_changes_from_an_older_owner_are_refused(self):
        a, b = self.a, self.b
        a.sync.offer("task:t1", b.id)
        exchange(a, b)
        b.sync.review()
        # a writes as if it still owned the task at epoch 1.
        with a.db.atomic():
            a.sync.applying.on = False
            stale = dict(a.db.get("tasks", "t1"), status="done")
            a.sync._append("tasks", "t1", "status", "done", 1)
            a.db.put("tasks", stale)
        exchange(a, b)
        self.assertEqual(b.db.get("tasks", "t1")["status"], "queued")
        self.assertTrue(any(r["kind"] == "Refused change from an older owner" for r in b.db.history()))

    def test_requests_reach_the_owner_and_requests_to_move_back(self):
        a, b = self.a, self.b
        self.assertEqual(b.sync.request_move("task:t1", b.id), "requested")
        exchange(b, a)
        a.sync.review()
        exchange(b, a)
        b.sync.review()
        self.assertTrue(b.sync.owns("task:t1"))


class FaultTests(Base):
    """Random partitions, crashes and handoffs. No takeover, so ownership must stay exclusive."""

    def run_simulation(self, n, seed, steps=400):
        rng = random.Random(seed)
        nodes = [self.node(f"n{i}") for i in range(n)]
        trust(*nodes)
        links = {(i, j): True for i in range(n) for j in range(n) if i < j}
        down = {}
        delivered = []
        items = []

        def alive(i):
            return down.get(i, 0) <= 0

        def check():
            for item in items:
                holders = [x.name for x in nodes if x.sync.owns(item)]
                self.assertLessEqual(len(holders), 1, f"seed {seed}: {item} owned by {holders}")
            self.assertEqual(len(delivered), len(set(delivered)), f"seed {seed}: duplicate send")

        def act(x, item):
            key = item.split(":", 1)[1]
            t = x.db.get("tasks", key)
            if not t or not x.sync.owns(item):
                return
            version = t["version"]
            intent = t.get("intent")
            if intent == version and not t.get("receipt"):
                # Reconcile a crash between intent and receipt with a verification read.
                with x.db.atomic():
                    x.db.put("tasks", dict(t, receipt=(key, version) in delivered or "verified absent"))
                return
            if intent == version:
                if rng.random() < 0.5:
                    with x.db.atomic():
                        x.db.put("tasks", dict(t, version=version + 1, receipt=False))
                return
            with x.db.atomic():
                x.db.put("tasks", dict(t, intent=version, receipt=False))
            if rng.random() < 0.1:
                x.crash()  # after the intent, before the send
                return
            delivered.append((key, version))
            with x.db.atomic():
                x.db.put("tasks", dict(x.db.get("tasks", key), receipt=True))

        for step in range(steps):
            op = rng.random()
            i = rng.randrange(n)
            x = nodes[i]
            for k in list(down):
                down[k] -= 1
            if op < 0.08 and alive(i):
                key = f"t{step}"
                with x.db.atomic():
                    x.db.put("tasks", dict(id=key, version=1, title=key))
                items.append("task:" + key)
            elif op < 0.35:
                j = rng.randrange(n)
                if i != j and alive(i) and alive(j) and links[tuple(sorted((i, j)))]:
                    exchange(x, nodes[j])
            elif op < 0.45:
                pair = rng.choice(list(links))
                links[pair] = not links[pair]
            elif op < 0.5:
                x.crash()
                down[i] = rng.randrange(1, 20)
            elif op < 0.65 and alive(i) and items:
                j = rng.randrange(n)
                if j != i:
                    x.sync.offer(rng.choice(items), nodes[j].id)
            elif op < 0.75 and alive(i):
                x.sync.review()
            elif op < 0.8:
                self.clock.advance(rng.choice([1, 30, sync.HANDOFF_TIMEOUT / 2]))
            elif alive(i) and items:
                act(x, rng.choice(items))
            check()

        # Heal everything and let handoffs finish.
        for _ in range(4):
            self.clock.advance(sync.HANDOFF_TIMEOUT + 1)
            settle(nodes)
        check()
        for item in items:
            owners = {x.sync.owner(item)["owner"] for x in nodes}
            self.assertEqual(len(owners), 1, f"seed {seed}: {item} disagrees on owner")
            self.assertEqual(sum(x.sync.owns(item) for x in nodes), 1, f"seed {seed}: {item} has no owner")
        return delivered

    def test_two_instances(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self.setUp()
                self.run_simulation(2, seed)

    def test_three_instances(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                self.setUp()
                self.run_simulation(3, 100 + seed)


class PairingTests(unittest.TestCase):
    """Real mutual TLS on loopback, with certificates made by openssl."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.vps = sync.Sync(Store(Path(self.tmp.name) / "vps"), name="VPS", secrets_api=MemorySecrets())
        self.laptop = sync.Sync(Store(Path(self.tmp.name) / "laptop"), name="Laptop", secrets_api=MemorySecrets())
        self.port = self.vps.listen("127.0.0.1", 0)
        self.address = f"127.0.0.1:{self.port}"
        self.addCleanup(self.vps.close)

    def test_pair_confirm_and_sync_both_ways(self):
        code = self.vps.create_code()
        info = self.laptop.join(self.address, code)
        self.assertEqual(info["name"], "VPS")
        laptop_view = self.laptop.peer(self.vps.id)
        vps_view = self.vps.peer(self.laptop.id)
        self.assertEqual(laptop_view["fingerprint"], self.vps.fingerprint)
        self.assertEqual(vps_view["fingerprint"], self.laptop.fingerprint)
        self.assertFalse(vps_view["trusted"])
        # Before confirmation the VPS refuses exchanges.
        self.laptop.exchange_all()
        self.assertIn("awaiting confirmation", self.laptop.peer(self.vps.id)["state"])
        self.vps.confirm(self.laptop.id)
        with self.laptop.db.atomic():
            self.laptop.db.put("tasks", dict(id="from-laptop", title="Laptop task"))
        with self.vps.db.atomic():
            self.vps.db.put("tasks", dict(id="from-vps", title="VPS task"))
        self.laptop.exchange_all()
        self.assertEqual(self.laptop.peer(self.vps.id)["state"], "connected")
        self.assertEqual(self.vps.db.get("tasks", "from-laptop")["title"], "Laptop task")
        self.assertEqual(self.laptop.db.get("tasks", "from-vps")["title"], "VPS task")
        self.assertEqual(self.vps.workspace_key(), self.laptop.workspace_key())

    def test_wrong_reused_and_expired_codes_fail(self):
        code = self.vps.create_code()
        wrong = "AAAA-" + code[5:] if not code.startswith("AAAA") else "BBBB-" + code[5:]
        with self.assertRaises(PermissionError):
            self.laptop.join(self.address, wrong)
        self.laptop.join(self.address, code)
        other = sync.Sync(Store(Path(self.tmp.name) / "other"), name="Other")
        with self.assertRaises(PermissionError):
            other.join(self.address, code)
        code = self.vps.create_code()
        with patch.object(self.vps, "now", lambda: time.time() + sync.CODE_TTL + 1):
            with self.assertRaises(PermissionError):
                other.join(self.address, code)

    def test_unpaired_client_certificate_cannot_exchange(self):
        stranger = sync.Sync(Store(Path(self.tmp.name) / "stranger"), name="Stranger")
        with stranger.db.atomic():
            stranger.db.put("peers", dict(id=self.vps.id, name="VPS", platform="linux",
                                          cert=self.vps.cert(), fingerprint=self.vps.fingerprint,
                                          trusted=True, address=self.address))
        stranger.exchange_all()
        self.assertNotEqual(stranger.peer(self.vps.id)["state"], "connected")
        self.assertEqual(self.vps.db.rows("tasks"), [])


class EngineBase(unittest.TestCase):
    """Two full engines linked in memory: ownership gates the scheduler, sends and dispatch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock(time.time())
        self.laptop = Engine(Path(self.tmp.name) / "laptop", clock=self.clock)
        self.vps = Engine(Path(self.tmp.name) / "vps", clock=self.clock)
        self.addCleanup(self.laptop.close)
        self.addCleanup(self.vps.close)
        for a, b in ((self.laptop, self.vps), (self.vps, self.laptop)):
            with a.db.atomic():
                a.db.put("peers", dict(id=b.instances.id, name="VPS" if b is self.vps else "Laptop",
                                       platform="test", cert="", fingerprint="x", trusted=True, address="memory"))
            a.instances.identity = False
            # No real agents, Hermes or desktop scans from the scheduler in these tests.
            for target, name in ((a.orchestrator, "due"), (a.suggestions, "run")):
                patcher = patch.object(target, name, return_value=None)
                patcher.start()
                self.addCleanup(patcher.stop)

    def link(self):
        for _ in range(2):
            self.laptop.instances.sync_with(
                self.vps.instances.id,
                lambda r: wire(self.vps.instances.handle_exchange(self.laptop.instances.id, wire(r))))
            self.laptop.instances.review(self.laptop.idle)
            self.vps.instances.review(self.vps.idle)



class EngineTests(EngineBase):
    def test_task_appears_on_the_peer_and_runs_only_on_its_owner(self):
        t = self.laptop.new_task("Draft the reply", self.laptop.global_project, "Body")
        self.link()
        self.assertEqual(self.vps.db.get("tasks", t["id"])["title"], "Draft the reply")
        with patch.object(self.vps, "process") as vps_process, patch.object(self.laptop, "process") as laptop_process:
            self.vps.tick()
            self.laptop.tick()
            for engine in (self.vps, self.laptop):
                for job in list(engine.jobs.values()):
                    job.result()
        self.assertFalse(vps_process.called)
        self.assertTrue(laptop_process.called)
        state = self.vps.state()
        self.assertEqual(next(x for x in state["tasks"] if x["id"] == t["id"])["owner"], "Laptop")

    def test_approval_on_a_non_owner_is_sent_by_the_owner(self):
        t = self.laptop.new_task("Reply", self.laptop.global_project, "Body", sample=True,
                                 status="review", draft="Thanks!", account="me", destination="you")
        self.link()
        version = self.vps.db.get("tasks", t["id"])["version"]
        result = self.vps.action(dict(op="approve", id=t["id"], version=version))
        self.assertEqual(result, {"relayed_to": "Laptop"})
        self.assertEqual(self.vps.db.get("tasks", t["id"])["status"], "review")
        self.link()
        self.laptop.tick()
        for job in list(self.laptop.jobs.values()):
            job.result()
        done = self.laptop.db.get("tasks", t["id"])
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["send_intent"]["instance"], self.laptop.instances.id)
        self.link()
        self.assertEqual(self.vps.db.get("tasks", t["id"])["status"], "done")

    def test_connection_polls_from_the_vps_after_a_handoff(self):
        connection = dict(id="c1", kind="email", label="Mail", config={}, status="Connected", next=0,
                          revision="r1")
        with self.laptop.db.atomic():
            self.laptop.db.put("connections", connection)
        with patch.object(connectors, "_secret", return_value="app-password"):
            self.link()
        self.assertEqual(self.vps.instances.owner_name("connection:c1"), "Laptop")
        self.laptop.action(dict(op="move", items="connections", to=self.vps.instances.id))
        self.link()
        self.link()
        self.assertTrue(self.vps.owns("connection:c1"))
        self.assertFalse(self.laptop.owns("connection:c1"))
        with patch.object(self.vps, "sync") as vps_poll, patch.object(self.laptop, "sync") as laptop_poll:
            self.vps.tick()
            self.laptop.tick()
            for engine in (self.vps, self.laptop):
                for job in list(engine.jobs.values()):
                    job.result()
        self.assertTrue(vps_poll.called)
        self.assertFalse(laptop_poll.called)

    def test_orchestrator_dispatch_is_forwarded_to_the_connection_owner(self):
        import app_mcp
        with self.vps.db.atomic():
            self.vps.db.put("connections", dict(id="c1", kind="email", label="Mail", config={},
                                                status="Connected", next=10**12, revision="r1"))
            self.vps.db.put("events", dict(id="e1", connection="c1", project="", state="seen", generation=1,
                                           raw=dict(key="k", title="Hi", body="Can you reply?", account="me",
                                                    destination="you", timestamp=0)))
        self.link()
        # Both claimed the orchestrator before pairing; the lower instance ID kept it. Give it to the laptop.
        if not self.laptop.owns(sync.ORCHESTRATOR):
            self.vps.action(dict(op="move", items="orchestrator", to=self.laptop.instances.id))
            self.link()
            self.link()
        # The laptop owns the orchestrator here, the VPS owns the mail connection.
        self.assertTrue(self.laptop.owns(sync.ORCHESTRATOR))
        self.assertFalse(self.vps.owns(sync.ORCHESTRATOR))
        session = self.laptop.mcp_sessions.get(self.laptop.mcp_sessions.issue(app_mcp.ORCHESTRATOR))
        result = self.laptop.mcp_tools.dispatch_task(session, dict(type="email_reply", evidence="e1",
                                                                    title="Reply to Hi", body=""))
        self.assertEqual(result["status"], "forwarded")
        self.assertIsNone(self.laptop.db.get("tasks", "e1"))
        self.link()
        with patch.object(self.vps.orchestrator, "due", return_value=None), \
                patch.object(self.vps, "process"), patch.object(self.vps.suggestions, "run"):
            self.vps.tick()
            for job in list(self.vps.jobs.values()):
                job.result()
        for job in list(self.vps.jobs.values()):
            job.result()
        self.assertEqual(self.vps.db.get("tasks", "e1")["task_type"], "email_reply")
        self.assertTrue(self.vps.owns("task:e1"))



class CapacityBase(EngineBase):
    """Phase 3: splitting queued work by capacity, pulling when idle, and handing over before an absence."""

    def setUp(self):
        super().setUp()
        for engine in (self.laptop, self.vps):
            provider = engine.settings()["provider"]
            patcher = patch.object(engine, "agent_states", return_value={provider: "ready", "hermes": "ready"})
            patcher.start()
            self.addCleanup(patcher.stop)
            patcher = patch.object(engine, "on_battery", return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.set(self.laptop, slots=1)
        self.set(self.vps, slots=2)

    def set(self, engine, **fields):
        with engine.db.atomic():
            engine.db.put("config", {**engine.settings(), **fields})

    def link(self):
        for _ in range(2):
            self.laptop.instances.sync_with(
                self.vps.instances.id,
                lambda r: wire(self.vps.instances.handle_exchange(self.laptop.instances.id, wire(r),
                                                                  self.vps.capacity())),
                self.laptop.capacity())
            self.laptop.instances.review(self.laptop.idle)
            self.vps.instances.review(self.vps.idle)

    def busy(self, engine):
        import concurrent.futures
        engine.jobs["task:running"] = concurrent.futures.Future()
        self.addCleanup(lambda: engine.jobs.pop("task:running", None))

    def queued(self, engine, title="Reply", **fields):
        return engine.new_task(title, engine.global_project, "Body", **fields)



class CapacityTests(CapacityBase):
    def test_full_laptop_hands_a_new_task_to_the_vps(self):
        self.busy(self.laptop)
        t = self.queued(self.laptop)
        self.link()
        self.laptop.balance()
        self.link()
        self.assertTrue(self.vps.owns("task:" + t["id"]))
        self.assertFalse(self.laptop.owns("task:" + t["id"]))

    def test_free_laptop_keeps_work_when_the_vps_is_overflow_only(self):
        self.set(self.vps, preference="overflow")
        t = self.queued(self.laptop)
        self.link()
        self.laptop.balance()
        self.link()
        self.assertTrue(self.laptop.owns("task:" + t["id"]))
        # A task created on the overflow-only VPS goes to the laptop while it has a free slot.
        v = self.queued(self.vps, "Made on the VPS")
        self.link()
        self.vps.balance()
        self.link()
        self.assertTrue(self.laptop.owns("task:" + v["id"]))

    def test_pinned_tasks_never_move(self):
        self.busy(self.laptop)
        local = self.queued(self.laptop, evidence=[{"source": "Git", "folder": "/tmp/repo"}])
        code = self.queued(self.laptop, kind="bugfix")
        self.link()
        self.laptop.balance()
        self.vps.balance()
        self.link()
        self.assertTrue(self.laptop.owns("task:" + local["id"]))
        self.assertTrue(self.laptop.owns("task:" + code["id"]))

    def test_idle_vps_pulls_work_from_a_full_laptop(self):
        t = self.queued(self.laptop)
        self.busy(self.laptop)
        self.laptop.recent_offers["task:" + t["id"]] = self.clock()  # the laptop has not offered it yet
        self.link()
        self.vps.balance()
        self.link()
        self.link()
        self.assertTrue(self.vps.owns("task:" + t["id"]))

    def test_slot_limit_bounds_running_tasks(self):
        import threading
        release = threading.Event()
        self.addCleanup(release.set)
        for i in range(3):
            self.queued(self.laptop, f"Task {i}")
        with patch.object(self.laptop, "process", side_effect=lambda key: release.wait(5)), \
                patch.object(self.laptop.orchestrator, "due", return_value=None):
            self.laptop.tick()
            self.assertEqual(self.laptop.running_tasks(), 1)
            release.set()

    def test_absence_hands_movable_items_to_the_vps(self):
        mail = dict(id="mail", kind="email", label="Mail", config={}, status="Connected", next=10**12, revision="r")
        gh = dict(id="gh", kind="github", label="Repo", config={"repository": "a/b"}, auth_method="gh_repository",
                  status="Connected", next=10**12, revision="r")
        with self.laptop.db.atomic():
            self.laptop.db.put("connections", mail)
            self.laptop.db.put("connections", gh)
        waiting = self.queued(self.laptop, status="review", draft="Hi", account="me", destination="you")
        queued = self.queued(self.laptop)
        local = self.queued(self.laptop, evidence=[{"source": "Git", "folder": "/tmp/repo"}])
        self.link()
        if not self.laptop.owns(sync.ORCHESTRATOR):
            self.vps.action(dict(op="move", items="orchestrator", to=self.laptop.instances.id))
            self.link()
            self.link()

        def exchange(capacity):
            self.link()

        handed, kept = self.laptop.prepare_absence("sleeping", timeout=5, exchange=exchange)
        for item in ("task:" + waiting["id"], "task:" + queued["id"], "connection:mail", sync.ORCHESTRATOR):
            self.assertTrue(self.vps.owns(item), item)
        self.assertTrue(self.laptop.owns("task:" + local["id"]))
        self.assertTrue(self.laptop.owns("connection:gh"))
        self.assertEqual(kept, 0)
        self.assertGreaterEqual(handed, 4)
        self.assertTrue(self.laptop.absent())
        self.assertEqual(self.laptop.capacity()["state"], "sleeping")
        with patch.object(self.laptop, "process") as process, \
                patch.object(self.laptop.orchestrator, "due", return_value=None):
            self.laptop.tick()
        self.assertFalse(process.called, "nothing new starts while preparing to sleep")
        self.clock.advance(120)
        self.assertFalse(self.laptop.absent(), "waking resumes work")

    def test_absence_without_a_reachable_peer_keeps_work(self):
        t = self.queued(self.laptop)
        self.assertEqual(self.laptop.prepare_absence("stopping", timeout=1, exchange=lambda capacity: None), (0, 0))
        self.assertTrue(self.laptop.owns("task:" + t["id"]))

    def test_run_on_moves_a_task(self):
        t = self.queued(self.laptop)
        self.link()
        self.vps.action(dict(op="move", items=["task:" + t["id"]], to=self.vps.instances.id))
        self.link()
        self.link()
        self.assertTrue(self.vps.owns("task:" + t["id"]))


GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid", GIT_CONFIG_GLOBAL=os.devnull)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=GIT_ENV).stdout


class WorktreeHandoffTests(CapacityBase):
    """A coding task moves with its worktree, uncommitted and untracked edits included."""

    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        origin = root / "origin.git"
        seed = root / "seed"
        git(root, "init", "-q", "--bare", "-b", "main", str(origin))
        git(root, "init", "-q", "-b", "main", str(seed))
        (seed / "app.py").write_text("print('v1')\n")
        (seed / "old.txt").write_text("remove me\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "seed")
        git(seed, "remote", "add", "origin", str(origin))
        git(seed, "push", "-q", "origin", "main")
        self.origin = origin
        self.laptop_checkout = root / "laptop-checkout"
        self.vps_checkout = root / "vps-checkout"
        for checkout in (self.laptop_checkout, self.vps_checkout):
            git(root, "clone", "-q", str(origin), str(checkout))
        self.laptop.action(dict(op="instance_checkout", path=str(self.laptop_checkout)))

    def coding_task(self):
        t = self.queued(self.laptop, "Fix the bug", kind="coding", status="question", question="Which approach?",
                        repository=str(self.laptop_checkout))
        tree = worker.prepare_worktree(self.laptop_checkout, self.laptop.db.root / "worktrees", t["id"])
        with self.laptop.db.atomic():
            self.laptop.db.put("tasks", {**self.laptop.db.get("tasks", t["id"]), "worktree": str(tree)})
        (tree / "lib.py").write_text("def helper():\n    return 1\n")
        git(tree, "add", "lib.py")
        git(tree, "commit", "-q", "-m", "Add helper")
        (tree / "app.py").write_text("print('v2')\n")
        (tree / "new.txt").write_text("untracked\n")
        (tree / "old.txt").unlink()
        return t, tree

    def test_task_moves_with_its_worktree(self):
        self.vps.action(dict(op="instance_checkout", path=str(self.vps_checkout)))
        t, tree = self.coding_task()
        self.link()
        self.assertIn(worker.normalize_origin(str(self.origin)) or str(self.origin), str(self.vps.capacity()))
        self.laptop.action(dict(op="move", items=["task:" + t["id"]], to=self.vps.instances.id))
        self.link()
        self.link()
        self.assertTrue(self.vps.owns("task:" + t["id"]))
        moved = self.vps.db.get("tasks", t["id"])
        new_tree = Path(moved["worktree"])
        self.assertEqual(moved["repository"], str(self.vps_checkout.resolve()))
        self.assertTrue(str(new_tree).startswith(str(Path(self.vps.db.root).resolve())))
        self.assertEqual((new_tree / "app.py").read_text(), "print('v2')\n")
        self.assertEqual((new_tree / "new.txt").read_text(), "untracked\n")
        self.assertEqual((new_tree / "lib.py").read_text(), "def helper():\n    return 1\n")
        self.assertFalse((new_tree / "old.txt").exists())
        status = git(new_tree, "status", "--porcelain")
        self.assertIn(" M app.py", status)
        self.assertIn("?? new.txt", status)
        self.assertIn(" D old.txt", status)
        self.assertIn("Add helper", git(new_tree, "log", "--oneline", "-1"))
        self.assertTrue((tree / "new.txt").exists(), "the sender keeps its worktree")
        self.assertEqual(self.vps.db.rows("bundles"), [])
        # The laptop's working index was not touched by the snapshot.
        self.assertIn("?? new.txt", git(tree, "status", "--porcelain"))

    def test_without_a_checkout_the_receiver_declines_and_the_owner_resumes(self):
        t, _ = self.coding_task()
        self.link()
        with self.assertRaises(ValueError):
            self.laptop.action(dict(op="move", items=["task:" + t["id"]], to=self.vps.instances.id))
        self.assertTrue(self.laptop.instances.offer("task:" + t["id"], self.vps.instances.id))
        self.assertFalse(self.laptop.owns("task:" + t["id"]))
        self.link()
        self.link()
        self.assertTrue(self.laptop.owns("task:" + t["id"]))
        self.assertFalse(self.vps.owns("task:" + t["id"]))
        self.assertTrue(any(r["kind"] == "Handoff declined" for r in self.vps.db.history()))

    def test_coding_task_without_repository_stays(self):
        self.busy(self.laptop)
        t = self.queued(self.laptop, kind="coding")
        self.vps.action(dict(op="instance_checkout", path=str(self.vps_checkout)))
        self.link()
        self.laptop.balance()
        self.link()
        self.assertTrue(self.laptop.owns("task:" + t["id"]))


class TakeoverTests(CapacityBase):
    def test_takeover_from_a_silent_instance(self):
        sending = self.queued(self.laptop, "Sending", status="review", draft="Hi", account="me", destination="you")
        working = self.queued(self.laptop, "Working")
        with self.laptop.db.atomic():
            self.laptop.db.put("tasks", {**self.laptop.db.get("tasks", sending["id"]), "status": "sending"})
            self.laptop.db.put("tasks", {**self.laptop.db.get("tasks", working["id"]), "status": "working"})
        self.link()
        laptop_id = self.laptop.instances.id
        with self.assertRaises(ValueError):
            self.vps.action(dict(op="instance_takeover", peer=laptop_id, confirm="Laptop"))  # in touch just now
        self.clock.advance(300)
        with self.assertRaises(ValueError):
            self.vps.action(dict(op="instance_takeover", peer=laptop_id, confirm="laptop?"))
        result = self.vps.action(dict(op="instance_takeover", peer=laptop_id, confirm=" laptop "))
        self.assertGreaterEqual(result["taken"], 2)
        for t in (sending, working):
            self.assertTrue(self.vps.owns("task:" + t["id"]))
            self.assertEqual(self.vps.instances.owner("task:" + t["id"])["epoch"], 3)
        self.assertEqual(self.vps.db.get("tasks", sending["id"])["status"], "uncertain")
        self.assertEqual(self.vps.db.get("tasks", working["id"])["status"], "queued")
        # The laptop was only paused. It acts once more with its old epoch, then reconnects.
        with self.laptop.db.atomic():
            self.laptop.db.put("tasks", {**self.laptop.db.get("tasks", working["id"]), "result": "stale work"})
        self.link()
        self.assertFalse(self.laptop.owns("task:" + working["id"]))
        vps_task = self.vps.db.get("tasks", working["id"])
        self.assertNotEqual(vps_task.get("result"), "stale work")
        self.assertEqual(vps_task["discarded"][-1]["value"], "stale work")
        self.link()
        self.assertEqual(self.laptop.db.get("tasks", working["id"])["discarded"][-1]["value"], "stale work")

    def test_takeover_of_a_removed_instance_and_a_single_task(self):
        a = self.queued(self.laptop, "A")
        b = self.queued(self.laptop, "B")
        self.link()
        self.vps.instances.remove(self.laptop.instances.id)
        self.assertEqual(self.vps.state()["instances"]["departed"][0]["name"], "Laptop")
        self.vps.action(dict(op="instance_takeover", peer=self.laptop.instances.id, confirm="Laptop",
                             items=["task:" + a["id"]]))
        self.assertTrue(self.vps.owns("task:" + a["id"]))
        self.assertFalse(self.vps.owns("task:" + b["id"]))

if __name__ == "__main__":
    unittest.main()
