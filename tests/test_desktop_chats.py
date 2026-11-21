import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import desktop_chats
from engine import Engine

SESSION = "ab05f8ff-4abc-4dd4-9de3-1acfa61b20e3"
NOW = 1800000000


class DesktopChats(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.home = Path(directory.name).resolve()
        self.engine = Engine(self.home / "workspace", lambda: NOW)
        self.addCleanup(self.engine.close)
        self.actions = self.engine.action
        self.service = self.engine.suggestions

    def write(self, rel, data, lines=False):
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(map(json.dumps, data)) + "\n" if lines else json.dumps(data))
        os.utime(path, (NOW, NOW))
        return path

    def claude(self):
        self.write(
            "Library/Application Support/Claude/claude-code-sessions/org/account/local_fixture.json",
            {"cliSessionId": SESSION},
        )

        def record(role, value, **extra):
            return dict(
                sessionId=SESSION,
                type=role,
                timestamp=NOW - 2,
                uuid="prompt",
                message={"content": value, "stop_reason": "end_turn"},
                **extra,
            )

        self.write(
            ".claude/projects/work/" + SESSION + ".jsonl",
            [
                record("user", "Check the budget"),
                record("user", "private instructions", isMeta=True),
                record("user", [{"type": "tool_result", "content": "private output"}]),
                record(
                    "assistant",
                    [
                        {"type": "thinking", "thinking": "private reasoning"},
                        {"type": "text", "text": "Figures still need review."},
                    ],
                ),
                record("user", "Unfinished turn"),
            ],
            True,
        )

    def chatgpt(self):
        def log(kind, payload):
            return {"type": kind, "timestamp": NOW - 1, "payload": payload}

        def user(text, kind):
            return log(
                "response_item",
                dict(
                    role="user",
                    content=[{"type": "input_text", "text": text}],
                    internal_chat_message_metadata_passthrough={
                        "turn_id": "turn",
                        "content_item_kinds": [kind],
                    },
                ),
            )

        for n, (origin, source) in enumerate(
            [("Codex Desktop", "vscode"), ("codex_exec", "exec"), ("Codex Desktop", {"subagent": "worker"})]
        ):
            self.write(
                f".codex/sessions/2027/01/01/rollout-{n}.jsonl",
                [
                    log("session_meta", dict(id="session-" + str(n), source=source, originator=origin)),
                    user("Prepare the filing" if not n else "private subagent run", "user.text"),
                    user("private environment and tools", "environments.environment_context"),
                    log(
                        "event_msg",
                        dict(type="task_complete", last_agent_message="Need the client signature."),
                    ),
                ],
                True,
            )

    def connect(self, key, folder=""):
        self.actions(dict(op="chat_source_config", provider=key, enabled=True, folder=folder))

    def test_work_sessions_reach_discovery_and_disconnect_is_scoped(self):
        self.claude()
        self.chatgpt()
        for provider in ("claude", "chatgpt"):
            self.connect(provider)

        def propose(role, payload, *unused):
            self.assertEqual(role, "suggestions")
            self.assertEqual({r["source"] for r in payload["evidence"]}, {"Claude", "ChatGPT"})
            self.assertNotIn("private", json.dumps(payload))
            self.assertNotIn("Unfinished turn", json.dumps(payload))
            return {
                "suggestions": [
                    dict(
                        title="Follow up on " + r["title"],
                        reason=r["detail"],
                        body=r["detail"],
                        anchor=r["id"],
                        evidence=[],
                        project="Global",
                    )
                    for r in payload["evidence"]
                ]
            }

        with (
            patch.object(Path, "home", return_value=self.home),
            patch.object(self.engine, "infer", side_effect=propose) as infer,
        ):
            self.service.run()
            self.service.run()
            infer.assert_called_once()
        pending = self.service.state()["pending"]
        self.assertEqual(len(pending), 2)
        self.actions(dict(op="suggestion_config", foreground=False, folders=[]))
        self.assertEqual(set(self.service.config()["chats"]), {"claude", "chatgpt"})
        self.actions(dict(op="chat_source_config", provider="chatgpt", enabled=False))
        (remaining,) = self.service.state()["pending"]
        self.assertEqual(remaining["evidence"][0]["source"], "Claude")
        self.actions(dict(op="suggestion_start", id=remaining["id"]))
        task = self.engine.db.get("tasks", remaining["id"])
        self.assertEqual(task["project"], "Global")
        self.assertFalse(task.get("destination"))
        self.assertEqual(task["evidence"][0]["source"], "Claude")

    def test_export_current_branch_and_three_hour_window(self):
        def node(role, at, body, parent):
            return dict(
                parent=parent,
                message=dict(
                    id=body,
                    author=dict(role=role),
                    content=dict(content_type="text", parts=[body]),
                    create_time=at,
                ),
            )

        mapping = {
            "old": node("user", NOW - 10801, "too old", None),
            "oldreply": node("assistant", NOW - 1, "reply to old message", "old"),
            "u": node("user", NOW - 10800, "Follow up", "oldreply"),
            "a": node("assistant", NOW, "Still pending", "u"),
            "other": node("assistant", NOW - 3, "discarded branch", "u"),
        }
        path = self.write(
            "exports/conversations-000.json", [dict(id="thread", current_node="a", mapping=mapping)]
        )
        self.connect("chatgpt", str(path.parent))

        def scan():
            rows, _ = desktop_chats.collect(
                "chatgpt", self.service.config()["chats"]["chatgpt"], NOW - 10800, NOW, self.home
            )
            return rows

        (first,) = scan()
        self.assertEqual(first["detail"], "You: Follow up\nAssistant: Still pending")
        self.assertEqual(scan()[0]["id"], first["id"])
        # A record written by the chat app tomorrow has no evidence in today's window.
        mapping["u"]["message"]["create_time"] = NOW + 1
        path.write_text(json.dumps([dict(id="thread", current_node="a", mapping=mapping)]))
        self.assertFalse(scan())

    def test_claude_export_and_unreadable_sources(self):
        export = self.write(
            "export/conversations.json",
            [
                dict(
                    uuid="thread",
                    chat_messages=[
                        dict(
                            sender="human",
                            uuid="u",
                            created_at="2027-01-15T07:59:59Z",
                            text="Review the report",
                        ),
                        dict(
                            sender="assistant",
                            uuid="a",
                            created_at="2027-01-15T08:00:00Z",
                            content=[
                                {"type": "text", "text": "One review outstanding"},
                                {"type": "tool_result", "text": "private"},
                            ],
                        ),
                    ],
                )
            ],
        )
        self.connect("claude", str(export.parent))
        stop = desktop_chats.timestamp("2027-01-15T08:00:00Z")
        rows, gaps = desktop_chats.collect(
            "claude", self.service.config()["chats"]["claude"], stop - 10800, stop, self.home
        )
        self.assertEqual(len(rows), 1)
        self.assertNotIn("private", rows[0]["detail"])
        self.assertTrue(gaps)
        export.unlink()
        target = self.write("elsewhere.json", [])
        export.symlink_to(target)
        rows, gaps = desktop_chats.collect(
            "claude", self.service.config()["chats"]["claude"], stop - 10800, stop, self.home
        )
        self.assertEqual(rows, [])
        self.assertTrue(any("export" in gap.lower() for gap in gaps))

    def test_disconnecting_while_inference_runs_discards_result(self):
        self.claude()
        self.connect("claude")

        def disconnect(role, payload, *unused):
            self.actions(dict(op="chat_source_config", provider="claude", enabled=False))
            return {
                "suggestions": [
                    dict(
                        title="Stale proposal",
                        reason="",
                        body="",
                        anchor=payload["evidence"][0]["id"],
                        evidence=[],
                        project="Global",
                    )
                ]
            }

        with (
            patch.object(Path, "home", return_value=self.home),
            patch.object(self.engine, "infer", side_effect=disconnect),
        ):
            self.service.run()
        self.assertEqual(self.service.state()["pending"], [])
