"""The app MCP server: sessions, capabilities, the executor boundary and the stdio bridge."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import agents
import app_mcp
import connectors
from engine import Engine, digest
import server

ROOT = Path(__file__).resolve().parents[1]


class Fixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.now = 1_800_000_000
        self.engine = Engine(self.directory.name, lambda: self.now)
        self.addCleanup(self.engine.close)
        self.db = self.engine.db
        self.engine.add_project("A", "")
        self.mail = dict(id="mail", kind="email", label="work mail", config={}, status="Connected")
        self.slack = dict(id="chat", kind="slack", label="team", config={}, status="Connected")
        for c in (self.mail, self.slack):
            self.db.put("connections", c)
        self.email = self.event(self.mail, "1", "Invoice please", "Can you send the March invoice?",
                                destination="client@example.com", context={"subject": "Invoice", "thread": "t1"})
        self.other = self.event(self.mail, "2", "Unrelated", "Lunch on Friday?",
                                destination="friend@example.com", context={"subject": "Lunch", "thread": "t2"})
        self.ping = self.event(self.slack, "3", "Status?", "Where is the release?", destination="C1/1.0")
        self.tools = self.engine.mcp_tools
        self.sessions = self.engine.mcp_sessions

    def event(self, conn, key, title, body, destination, context=None):
        raw = dict(key=key, timestamp=self.now, title=title, body=body, destination=destination,
                   account="me@example.com", authored=False, owned=False, context=context or {})
        with patch("engine.worker.run_agent", return_value={"relevant": False}):
            self.engine.ingest(conn, raw)
        e = self.db.get("events", digest(conn["id"], key))
        e["project"] = "A"
        self.db.put("events", e)
        return e["id"]

    def orchestrator(self):
        return self.sessions.get(self.sessions.issue(app_mcp.ORCHESTRATOR))

    def call(self, session, name, **args):
        reply = app_mcp.handle(self.tools, session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                     "params": {"name": name, "arguments": args}})
        result = reply["result"]
        out = json.loads(result["content"][0]["text"])
        return out, result["isError"] or "refused" in out

    def dispatch(self, kind="email_reply", evidence=None):
        out, error = self.call(self.orchestrator(), "dispatch_task", type=kind, title="Reply to client",
                               evidence=evidence or self.email)
        self.assertFalse(error, out)
        return out["task"]

    def task_session(self, task):
        return self.sessions.get(self.sessions.issue(app_mcp.TASK, task, self.engine.task_actions(task)))


class SessionTests(Fixture):
    def test_tokens_are_bound_to_a_role_and_revocable(self):
        with self.assertRaises(ValueError):
            self.sessions.issue(app_mcp.TASK)
        with self.assertRaises(ValueError):
            self.sessions.issue("admin")
        token = self.sessions.issue(app_mcp.ORCHESTRATOR)
        self.assertEqual(self.sessions.get(token).role, app_mcp.ORCHESTRATOR)
        self.sessions.revoke(token)
        self.assertIsNone(self.sessions.get(token))
        self.assertIsNone(self.sessions.get("guess"))
        token = self.sessions.issue(app_mcp.TASK, "t", ())
        self.sessions.get(token).expires = 0
        self.assertIsNone(self.sessions.get(token))

    def test_protocol_and_tools_visible_per_role(self):
        session = self.orchestrator()
        init = app_mcp.handle(self.tools, session, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                                     "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIsNone(app_mcp.handle(self.tools, session, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = lambda s: {t["name"] for t in app_mcp.handle(
            self.tools, s, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]}
        orchestrator = names(session)
        self.assertIn("dispatch_task", orchestrator)
        self.assertFalse(orchestrator & {"send_email", "send_message", "comment", "complete_task"})
        task = names(self.task_session(self.dispatch()))
        self.assertIn("send_email", task)
        self.assertFalse(task & {"send_message", "comment", "dispatch_task", "search_activity"})
        unknown = app_mcp.handle(self.tools, session, {"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(unknown["error"]["code"], -32601)


class OrchestratorTests(Fixture):
    def test_reads_search_and_dispatches_each_item_once(self):
        session = self.orchestrator()
        hits, _ = self.call(session, "search_activity", query="invoice")
        self.assertEqual([h["id"] for h in hits], [self.email])
        item, _ = self.call(session, "read_item", id=self.email)
        self.assertEqual(item["body"], "Can you send the March invoice?")
        task = self.dispatch()
        t = self.db.get("tasks", task)
        self.assertEqual((t["status"], t["kind"], t["task_type"], t["account"], t["destination"]),
                         ("queued", "reply", "email_reply", "me@example.com", "client@example.com"))
        again, _ = self.call(session, "dispatch_task", type="email_reply", title="Again", evidence=self.email)
        self.assertEqual(again["status"], "exists")
        self.assertEqual(sum(1 for x in self.db.rows("tasks") if x.get("event") == self.email), 1)

    def test_owned_or_unknown_evidence_is_refused(self):
        e = self.db.get("events", self.other)
        e["raw"]["owned"] = True
        self.db.put("events", e)
        out, error = self.call(self.orchestrator(), "dispatch_task", type="email_reply", title="x",
                               evidence=self.other)
        self.assertTrue(error)
        out, error = self.call(self.orchestrator(), "dispatch_task", type="wire_money", title="x",
                               evidence=self.email)
        self.assertTrue(error)
        self.assertEqual(self.db.rows("tasks"), [])

    def test_orchestrator_cannot_ask_the_user_to_decide(self):
        names = {t["name"] for t in app_mcp.handle(self.tools, self.orchestrator(),
                 {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
        self.assertFalse(names & {"propose_task", "ask_user"})

    def test_refusals_do_not_count_as_orchestrator_errors(self):
        """Hermes stops calling a server after three error results; a refusal must not trip that."""
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "send_email", "arguments": {}}}
        reply = app_mcp.handle(self.tools, self.orchestrator(), call)["result"]
        self.assertFalse(reply["isError"])
        self.assertIn("refused", json.loads(reply["content"][0]["text"]))
        task = app_mcp.Session(app_mcp.TASK, "t1")
        self.assertTrue(app_mcp.handle(self.tools, task, call)["result"]["isError"])

    def test_orchestrator_cannot_send(self):
        out, error = self.call(self.orchestrator(), "send_email", text="hi")
        self.assertTrue(error)
        self.assertIn("not available", out["refused"])


class TaskAgentTests(Fixture):
    def test_reads_are_limited_to_its_own_task_and_thread(self):
        task = self.dispatch()
        other = self.dispatch("chat_reply", self.ping)
        session = self.task_session(task)
        self.assertEqual([t["id"] for t in self.call(session, "list_tasks")[0]], [task])
        self.assertTrue(self.call(session, "read_task", id=other)[1])
        self.assertTrue(self.call(session, "read_item", id=self.other)[1])
        self.assertFalse(self.call(session, "read_item", id=self.email)[1])
        self.assertTrue(self.call(session, "search_activity", query="lunch")[1])
        thread, _ = self.call(session, "read_thread")
        self.assertEqual(thread["source"]["body"], "Can you send the March invoice?")

    def test_dry_mode_send_becomes_a_review_and_writes_nothing(self):
        task = self.dispatch()
        session = self.task_session(task)
        with patch.object(connectors, "send") as send:
            out, error = self.call(session, "send_email", text="Attached is the March invoice.")
        self.assertFalse(error, out)
        self.assertEqual(out["status"], "pending_review")
        send.assert_not_called()
        t = self.db.get("tasks", task)
        self.assertEqual((t["status"], t["draft"], t["version"], t["subject"]),
                         ("review", "Attached is the March invoice.", out["action_version"], "Re: Invoice"))
        self.assertEqual((t["account"], t["destination"]), ("me@example.com", "client@example.com"))
        # The user approves exactly this version; the executor sends once.
        with patch.object(connectors, "send", return_value="<msg-1@example.com>") as send:
            self.engine.deliver(task, out["action_version"])
        send.assert_called_once()
        self.assertEqual(self.db.get("tasks", task)["status"], "done")

    def test_rule_authorized_send_is_dispatched_once(self):
        self.engine.action({"op": "settings", "dry": False})
        self.engine.action({"op": "rule", "project": "A", "kind": "reply", "mode": "auto"})
        task = self.dispatch()
        session = self.task_session(task)
        with patch.object(connectors, "send", return_value="<msg-2@example.com>") as send:
            out, error = self.call(session, "send_email", text="Here it is.")
            self.assertEqual((out["status"], out["receipt"]), ("sent", "<msg-2@example.com>"))
            out, error = self.call(session, "send_email", text="Here it is again.")
        self.assertTrue(error)
        send.assert_called_once()

    def test_uncertain_send_blocks_a_resend(self):
        self.engine.action({"op": "settings", "dry": False})
        self.engine.action({"op": "rule", "project": "A", "kind": "reply", "mode": "auto"})
        task = self.dispatch()
        session = self.task_session(task)
        with patch.object(connectors, "send", side_effect=TimeoutError("timed out")) as send:
            out, _ = self.call(session, "send_email", text="Here it is.")
            self.assertEqual(out["status"], "uncertain")
            out, _ = self.call(session, "send_email", text="Here it is.")
            self.assertEqual(out["status"], "uncertain")
        send.assert_called_once()

    def test_wrong_channel_and_finished_sessions_are_refused(self):
        task = self.dispatch("follow_up")
        session = self.task_session(task)
        out, error = self.call(session, "send_message", text="hi")
        self.assertTrue(error)
        self.assertIn("cannot deliver", out["refused"])
        self.assertTrue(self.call(session, "complete_task", summary="done")[1])
        self.assertIn("Agent tool refused", {row["kind"] for row in self.db.history()})

    def test_question_and_skip(self):
        task = self.dispatch()
        session = self.task_session(task)
        self.assertEqual(self.call(session, "ask_user", question="Which month?")[0]["status"], "question")
        t = self.db.get("tasks", task)
        self.assertEqual((t["status"], t["question"]), ("question", "Which month?"))
        self.call(session, "skip_task", reason="Finance already replied")
        self.assertEqual(self.db.get("tasks", task)["status"], "skipped")


class EngineWiringTests(Fixture):
    def test_task_runs_get_a_scoped_session_only_with_a_running_server(self):
        task = self.dispatch()
        seen = []

        def agent(role, payload, provider, workdir=None, mcp=None, **_):
            seen.append(mcp)
            if mcp:
                session = self.sessions.get(mcp["token"])
                self.assertEqual((session.role, session.task, set(session.actions)),
                                 (app_mcp.TASK, task, {"send_email"}))
            return {"outcome": "done", "text": "ok", "kind": "summary", "commitments": []}

        with patch("engine.worker.run_agent", side_effect=agent):
            self.engine.infer("task", {}, "A", task)
            self.engine.mcp_url = "http://127.0.0.1:1/mcp"
            self.engine.infer("task", {}, "A", task)
        self.assertIsNone(seen[0])
        self.assertIsNone(self.sessions.get(seen[1]["token"]))


class TransportTests(Fixture):
    def setUp(self):
        super().setUp()
        self.http = server.HTTP(self.engine)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def post(self, body, **headers):
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=10)
        connection.request("POST", "/mcp", body=json.dumps(body), headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_endpoint_requires_a_session_token_and_rejects_browsers(self):
        token = self.sessions.issue(app_mcp.ORCHESTRATOR)
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual(self.post(message)[0], 403)
        self.assertEqual(self.post(message, Authorization="Bearer wrong")[0], 403)
        auth = "Bearer " + token
        self.assertEqual(self.post(message, Authorization=auth, Origin=self.http.origin)[0], 403)
        self.assertEqual(self.post(message, Authorization=auth, Cookie="memyself_session=" + self.http.session)[0], 403)
        status, data = self.post(message, Authorization=auth)
        self.assertEqual(status, 200)
        self.assertIn("dispatch_task", data.decode())

    def test_stdio_bridge_reads_the_token_from_a_private_file(self):
        token = self.sessions.issue(app_mcp.ORCHESTRATOR)
        token_file = Path(self.directory.name) / "token"
        token_file.write_text(token)
        entry = app_mcp.launch_config(self.engine.mcp_url, token_file)
        self.assertNotIn(token, json.dumps(entry))
        lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                 {"jsonrpc": "2.0", "method": "notifications/initialized"},
                 {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "search_activity", "arguments": {"query": "invoice"}}}]
        done = subprocess.run([entry["command"], *entry["args"]], input="\n".join(map(json.dumps, lines)) + "\n",
                              capture_output=True, text=True, timeout=30,
                              env={**os.environ, **entry["env"]})
        replies = [json.loads(line) for line in done.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertIn("Invoice please", replies[1]["result"]["content"][0]["text"])

    def test_a_long_lived_bridge_uses_each_runs_token(self):
        """Hermes' gateway keeps the bridge between orchestrator runs; each run writes a new token."""
        token_file = Path(self.directory.name) / "mcp-token"
        token_file.write_text(self.sessions.issue(app_mcp.ORCHESTRATOR))
        entry = app_mcp.launch_config(self.engine.mcp_url, token_file)
        bridge = subprocess.Popen([entry["command"], *entry["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  text=True, env={**os.environ, **entry["env"]})
        self.addCleanup(bridge.wait, 10)
        self.addCleanup(bridge.stdin.close)

        def call(ident):
            bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident, "method": "tools/list"}) + "\n")
            bridge.stdin.flush()
            return json.loads(bridge.stdout.readline())

        self.assertIn("result", call(1))
        self.sessions.revoke(token_file.read_text())  # The first run ends.
        token_file.unlink()
        self.assertIn("refused", call(2)["error"]["message"])
        token_file.write_text(self.sessions.issue(app_mcp.ORCHESTRATOR))  # The next run starts.
        self.assertIn("result", call(3))


class AgentSetupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.settings = {"provider": "claude"}
        self.agents = agents.Agents(self.root, lambda: self.settings)

    def fake(self, installed, outputs):
        def run(args, env=None):
            name = Path(args[0]).name
            key = (name, *args[1:3])
            return outputs.get(key, (0, "", ""))
        return (patch("agents.executable", side_effect=lambda n, root=None: "/bin/" + n if n in installed else None),
                patch("agents._run", side_effect=run))

    def states(self):
        return {h["id"]: h["state"] for h in self.agents.inventory(refresh=True)["harnesses"]}

    def test_one_claude_sign_in_makes_task_agents_and_hermes_ready(self):
        outputs = {("claude", "--version"): (0, "2.1.330 (Claude Code)", ""),
                   ("claude", "auth", "status"): (0, '{"loggedIn": true}', ""),
                   ("hermes", "--version"): (0, "Hermes Agent v0.23.3", "")}
        a, b = self.fake({"claude", "hermes"}, outputs)
        with a, b:
            self.assertEqual(self.states(), {"claude": "ready", "codex": "not_installed", "hermes": "ready"})
            outputs[("claude", "auth", "status")] = (1, '{"loggedIn": false}', "")
            states = self.states()
        self.assertEqual((states["claude"], states["hermes"]), ("sign_in", "sign_in"))

    def test_codex_subscription_needs_its_own_hermes_sign_in(self):
        self.settings["orchestrator_subscription"] = "codex"
        outputs = {("codex", "--version"): (0, "codex-cli 0.166.0", ""),
                   ("codex", "login", "status"): (0, "Logged in using ChatGPT", ""),
                   ("hermes", "--version"): (0, "Hermes Agent v0.23.3", ""),
                   ("hermes", "auth", "status"): (0, "openai-codex: logged out (No Codex credentials stored.)", "")}
        a, b = self.fake({"codex", "hermes"}, outputs)
        with a, b:
            inventory = self.agents.inventory(refresh=True)
        rows = {h["id"]: h for h in inventory["harnesses"]}
        self.assertEqual((rows["codex"]["state"], rows["hermes"]["state"]), ("ready", "sign_in"))
        self.assertIn("auth add openai-codex", rows["hermes"]["sign_in"])
        self.assertIn("HERMES_HOME=" + str(self.root / "hermes"), rows["hermes"]["sign_in"])

    def test_old_hermes_is_unsupported(self):
        outputs = {("claude", "auth", "status"): (0, '{"loggedIn": true}', ""),
                   ("hermes", "--version"): (0, "Hermes Agent v0.12.9", "")}
        a, b = self.fake({"claude", "hermes"}, outputs)
        with a, b:
            self.assertEqual(self.states()["hermes"], "unsupported_version")

    def test_hermes_config_is_private_and_limited_to_the_app_server(self):
        path = self.agents.write_hermes_config(["python3", "app_mcp.py"], {"MEMYSELFAI_MCP_TOKEN_FILE": "/t"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        config = json.loads(path.read_text())
        self.assertEqual(config["model"]["provider"], "anthropic")
        self.assertTrue(config["model"]["default"])  # An empty model name is rejected by the provider.
        self.assertEqual(config["approvals"]["mode"], "manual")
        self.assertEqual(set(config["mcp_servers"]), {"memyselfai"})
        with patch("agents.executable", return_value="/bin/hermes"):
            command, env = self.agents.hermes_command("Sweep")
        toolsets = command[command.index("-t") + 1].split(",")
        self.assertNotIn("terminal", toolsets)
        self.assertNotIn("file", toolsets)
        self.assertIn("memyselfai", toolsets)
        self.assertEqual(env["HERMES_HOME"], str(self.root / "hermes"))


if __name__ == "__main__":
    unittest.main()
