"""Scheduled Hermes runs against the real app MCP server, with a stand-in hermes executable."""

import datetime
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import app_mcp
from engine import Engine, digest
import orchestrator
import server

FAKE_HERMES = r'''
import json, os, subprocess, sys
args = sys.argv[1:]
prompt = args[args.index("-z") + 1]
toolsets = args[args.index("-t") + 1].split(",")
assert "memyselfai" in toolsets and not {"terminal", "file", "browser", "web", "messaging"} & set(toolsets)
home = os.environ["HERMES_HOME"]
mode = json.load(open(os.path.join(home, "fake.json")))
if mode.get("fail"):
    sys.stderr.write("model unavailable\n")
    sys.exit(1)
config = json.load(open(os.path.join(home, "config.yaml")))
assert list(config["mcp_servers"]) == ["memyselfai"]
server = config["mcp_servers"]["memyselfai"]
assert not any("token" in str(a).lower() and len(str(a)) > 40 for a in server["args"])
bridge = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          env={**os.environ, **server["env"]}, text=True)
count = 0
def call(name, arguments):
    global count
    count += 1
    bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "id": count, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments}}) + "\n")
    bridge.stdin.flush()
    result = json.loads(bridge.stdout.readline())["result"]
    return json.loads(result["content"][0]["text"]), result["isError"]
bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}}) + "\n")
bridge.stdin.flush()
bridge.stdout.readline()
hits, _ = call("search_activity", {"query": "invoice"})
outcomes = []
for hit in hits[: mode.get("dispatches", 1)]:
    out, error = call("dispatch_task", {"type": "email_reply", "title": "Reply: " + hit["title"], "evidence": hit["id"]})
    outcomes.append("refused" if error else out["status"])
sent, error = call("send_email", {"text": "x"})
outcomes.append("send refused" if error else "send allowed")
bridge.stdin.close()
bridge.wait()
print("Outcomes: " + ", ".join(outcomes) + " | " + prompt[-60:])
'''


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # 07:00 local today, before the digest time, unless a test moves the clock.
        self.now = datetime.datetime.now().astimezone().replace(hour=7, minute=0, second=0).timestamp()
        self.engine = Engine(self.root / "data", lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.orch = self.engine.orchestrator
        self.mail = dict(id="mail", kind="email", label="work", config={}, status="Connected")
        self.db.put("connections", self.mail)
        for key, title in (("1", "Invoice for March"), ("2", "Invoice for April")):
            raw = dict(key=key, timestamp=self.now - 3600, title=title, body="Please send the invoice.",
                       destination="client@example.com", account="me@example.com", authored=False,
                       owned=False, context={"subject": title, "thread": key})
            self.engine.ingest(self.mail, raw)
        binary = self.root / "bin" / "hermes"
        binary.parent.mkdir()
        binary.write_text("#!" + sys.executable + "\n" + FAKE_HERMES)
        binary.chmod(0o700)
        self.mode({})
        ready = {"harnesses": [{"id": "hermes", "state": "ready"}]}
        for context in (patch("agents.executable", return_value=str(binary)),
                        patch.object(self.engine.agents, "inventory", return_value=ready)):
            context.start()
            self.addCleanup(context.stop)
        self.http = server.HTTP(self.engine)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def mode(self, value):
        home = self.root / "data" / "hermes"
        home.mkdir(parents=True, exist_ok=True)
        (home / "fake.json").write_text(json.dumps(value))

    def history(self, kind):
        return [r["detail"] for r in self.db.history() if r["kind"] == kind]

    def test_schedule(self):
        self.engine.mcp_url, url = None, self.engine.mcp_url
        self.assertIsNone(self.orch.due(self.now))
        self.engine.mcp_url = url
        self.assertEqual(self.orch.due(self.now), "sweep")
        self.orch.save(last_sweep=self.now, signature=self.orch.signature())
        self.assertIsNone(self.orch.due(self.now + orchestrator.SWEEP_SECONDS))
        self.orch.save(enabled=False)
        self.orch.save(signature="")
        self.assertIsNone(self.orch.due(self.now + orchestrator.SWEEP_SECONDS))
        self.orch.save(enabled=True)
        self.assertIsNone(self.orch.due(self.now + 30))
        self.orch.ask("What is on my plate?")
        self.assertEqual(self.orch.due(self.now + 30), "prompt")
        with self.assertRaises(ValueError):
            self.orch.ask("And tomorrow?")
        self.orch.save(pending="")
        later = datetime.datetime.fromtimestamp(self.now).replace(hour=9).timestamp()
        self.assertEqual(self.orch.due(later), "digest")

    def test_sweep_dispatches_through_the_app_server_and_cleans_up(self):
        self.orch.run("sweep")
        tasks = [t for t in self.db.rows("tasks") if t.get("origin") == "orchestrator"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual((tasks[0]["task_type"], tasks[0]["status"]), ("email_reply", "queued"))
        summary = self.history("Orchestrator sweep")[0]
        self.assertIn("queued, send refused", summary)
        config = self.orch.config()
        self.assertEqual((config["status"], config["last_sweep"]), ("idle", self.now))
        self.assertEqual(self.engine.mcp_sessions.active, {})
        home = self.root / "data" / "hermes"
        self.assertEqual([p.name for p in home.iterdir() if p.name.startswith("run-")], [])
        self.assertEqual(stat.S_IMODE((home / "config.yaml").stat().st_mode), 0o600)
        self.assertIsNone(self.orch.due(self.now + 200))

    def test_daily_budget_limits_new_work(self):
        self.orch.configure({"budget": 1})
        self.mode({"dispatches": 2})
        self.orch.run("sweep")
        self.assertIn("queued, refused", self.history("Orchestrator sweep")[0])
        self.assertEqual(sum(1 for t in self.db.rows("tasks") if t.get("origin") == "orchestrator"), 1)
        self.assertEqual(self.orch.state()["used_today"], 1)

    def test_prompt_reply_goes_to_the_thread(self):
        self.orch.ask("What is on my plate this week?")
        self.orch.run("prompt")
        roles = [(m["role"], m["text"]) for m in self.orch.messages()]
        self.assertEqual(roles[0], ("user", "What is on my plate this week?"))
        self.assertEqual(roles[1][0], "hermes")
        self.assertIn("What is on my plate this week?", roles[1][1])
        self.assertFalse(self.orch.config()["pending"])

    def test_failure_backs_off_and_answers_the_user(self):
        self.mode({"fail": True})
        self.orch.ask("Hello?")
        self.orch.run("prompt")
        config = self.orch.config()
        self.assertEqual((config["status"], config["failures"]), ("failed", 1))
        self.assertEqual(config["retry_at"], self.now + orchestrator.BACKOFF[0])
        self.assertIn("model unavailable", config["error"])
        self.assertEqual(self.orch.messages()[-1]["role"], "app")
        self.assertIsNone(self.orch.due(self.now + 10))
        self.assertEqual(self.engine.mcp_sessions.active, {})

    def test_waits_when_hermes_is_not_ready(self):
        with patch.object(self.engine.agents, "inventory",
                          return_value={"harnesses": [{"id": "hermes", "state": "sign_in"}]}):
            self.orch.ask("Hello?")
            self.orch.run("prompt")
        self.assertEqual(self.orch.config()["status"], "waiting")
        self.assertIn("sign in", self.orch.messages()[-1]["text"])
        self.assertEqual(self.db.rows("tasks"), [])


if __name__ == "__main__":
    unittest.main()
