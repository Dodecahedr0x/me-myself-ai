import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import worker


class LocalAgentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.bin = self.root / ".local/bin"
        self.bin.mkdir(parents=True)
        self.codex_home = self.root / ".codex"
        self.codex_home.mkdir()
        for name in ("auth.json", "auth.lock", "config.toml", "AGENTS.md"):
            (self.codex_home / name).write_text("fixture")
        for context in (
            patch.dict(os.environ, {"PATH": os.defpath, "HOME": str(self.root),
                                    "GITHUB_TOKEN": "fixture", "CUSTOM_SECRET": "fixture"}, clear=True),
            patch("worker.DESKTOP_CLI_DIRS", ()),
        ):
            context.start()
            self.addCleanup(context.stop)

    def cli_code(self, provider, script):
        cli = self.bin / provider
        cli.write_text(f"#!{sys.executable}\n" + script)
        cli.chmod(0o700)

    def test_both_clis_receive_only_task_context_and_provider_credentials(self):
        script = """
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
def option(name):
    return args[args.index(name) + 1]
def values(name):
    return {args[i+1] for i in range(len(args)) if args[i] == name}
assert not os.listdir() and not Path.cwd().joinpath('.git').exists()
assert not {'GITHUB_TOKEN', 'CUSTOM_SECRET', 'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL'} & os.environ.keys()
assert 'fictional fixture' in sys.stdin.read() and 'fictional fixture' not in str(args)
result = {'outcome': 'done', 'text': os.getcwd(), 'kind': 'summary', 'commitments': []}
if Path(sys.argv[0]).name == 'claude':
    assert '--safe-mode' in args and '--restricted' in args and '--strict-mcp-config' in args
    assert option('--tools') == '' and option('--setting-sources') == ''
    assert '--disable-slash-commands' in args and '--no-session-persistence' in args
    assert option('--permission-mode') == 'dontAsk' and option('--permission-prompts') == 'none'
    assert 'outcome' in json.loads(option('--json-schema'))['required']
    assert os.environ['CLAUDE_CODE_OAUTH_TOKEN'] == 'test' and 'CODEX_API_KEY' not in os.environ
    print(json.dumps({'type':'result', 'subtype':'success', 'is_error':False, 'structured_output':result}))
else:
    assert args[-1] == '-' and 'exec' in args and '--no-daemon' in args and '--ephemeral' in args
    assert '--ignore-user-config' in args and '--ignore-rules' in args
    assert option('--ask-for-approval') == 'never'
    assert {'default_permissions="prepare"', 'permissions.prepare.filesystem={ ":root" = "deny" }',
            'permissions.prepare.network.enabled=false'} <= values('-c')
    assert {'hooks','plugins','shell_tool','unified_exec','apps','browser_use','computer_use','multi_agent',
            'image_generation','view_image','goals'} <= values('--disable')
    assert 'web_search="disabled"' in values('-c') and 'tools.experimental_request_user_input.enabled=false' in values('-c')
    assert os.environ['CODEX_API_KEY'] == 'test' and 'CLAUDE_CODE_OAUTH_TOKEN' not in os.environ
    config = Path(os.environ['CODEX_HOME'])
    assert {f.name for f in config.iterdir()} == {'auth.json','auth.lock'}
    assert all((config/name).is_symlink() for name in ('auth.json','auth.lock'))
    schema = json.loads(Path(option('--output-schema')).read_text())
    assert set(schema['required']) == schema['properties'].keys()
    assert schema['properties']['files']['anyOf'][1] == {'type': 'null'}
    result.update({key:None for key in schema['required'] if key not in result})
    Path(option('--output-last-message')).write_text(json.dumps(result))
    print('progress events must not be parsed as the proposal')
"""
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "test", "CODEX_API_KEY": "test",
                                     "ANTHROPIC_BASE_URL": "wrong", "OPENAI_BASE_URL": "wrong"}):
            for provider in worker.PROVIDERS:
                self.cli_code(provider, script)
                with self.subTest(provider=provider):
                    results = [worker.run_agent("task", {"source": "fictional fixture"}, provider)
                               for _ in range(2)]
                    self.assertNotEqual(results[0]["text"], results[1]["text"])
                    self.assertFalse(Path(results[0]["text"]).exists())
                    self.assertNotIn("files", results[0])

    def test_app_mcp_server_reaches_both_clis_without_exposing_its_token(self):
        script = """
import json, os, stat, sys
from pathlib import Path
args = sys.argv[1:]
assert 'secret-token' not in ' '.join(args) and 'secret-token' not in str(dict(os.environ))
assert 'memyselfai tools' in sys.stdin.read()
result = {'outcome': 'done', 'text': 'ok', 'kind': 'summary', 'commitments': []}
if Path(sys.argv[0]).name == 'claude':
    assert '--safe-mode' not in args and '--restricted' in args and '--strict-mcp-config' in args
    assert args[args.index('--tools') + 1] == ''
    assert args[args.index('--allowedTools') + 1] == 'mcp__memyselfai'
    server = json.loads(Path(args[args.index('--mcp-config') + 1]).read_text())['mcpServers']['memyselfai']
    token_file = Path(server['env']['MEMYSELFAI_MCP_TOKEN_FILE'])
    assert server['env']['MEMYSELFAI_MCP_URL'] == 'http://127.0.0.1:9/mcp'
    print(json.dumps({'type':'result', 'subtype':'success', 'is_error':False, 'structured_output':result}))
else:
    options = [args[i+1] for i in range(len(args)) if args[i] == '-c']
    env = next(o for o in options if o.startswith('mcp_servers.memyselfai.env='))
    assert any(o.startswith('mcp_servers.memyselfai.command=') for o in options)
    token_file = Path(env.split('MEMYSELFAI_MCP_TOKEN_FILE = ')[1].split('"')[1])
    out = Path(args[args.index('--output-last-message') + 1])
    schema = json.loads(Path(args[args.index('--output-schema') + 1]).read_text())
    result.update({key: None for key in schema['required'] if key not in result})
    out.write_text(json.dumps(result))
assert token_file.read_text() == 'secret-token'
assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
"""
        with patch.dict(os.environ, {"CODEX_API_KEY": "test"}):
            for provider in worker.PROVIDERS:
                self.cli_code(provider, script)
                with self.subTest(provider=provider):
                    result = worker.run_agent("task", {"source": "fixture"}, provider,
                                              mcp={"url": "http://127.0.0.1:9/mcp", "token": "secret-token"})
                    self.assertEqual(result["text"], "ok")

    def test_retryable_failures(self):
        for provider in worker.PROVIDERS:
            for script in ("raise SystemExit(2)", "print('invalid')", "print('[]')"):
                self.cli_code(provider, script)
                with self.subTest(provider=provider, script=script), self.assertRaises(worker.WorkerError):
                    worker.run_agent("discovery", {}, provider)
            self.cli_code(provider, "import time; time.sleep(10)")
            with patch("worker.AGENT_TIMEOUT", 0.05), self.assertRaisesRegex(worker.WorkerError, "timed out"):
                worker.run_agent("task", {}, provider)

    def test_error_envelopes_missing_fields_and_invalid_proposals(self):
        for envelope in (
            {"type":"result", "subtype":"error_during_execution", "is_error":True,
             "structured_output":{"body":"bad"}},
            {"type":"result", "subtype":"success", "is_error":False, "result":"ambiguous raw output"},
            {"type":"result", "subtype":"success", "is_error":False, "structured_output":{"body":5}},
        ):
            self.cli_code("claude", "print(" + repr(json.dumps(envelope)) + ")")
            with self.assertRaises(worker.WorkerError):
                worker.run_agent("style", {}, "claude")
        for result in ('{"body": 15}', '{"notBody":"wrong"}', 'not-json', 'x' * (worker.MAX_OUTPUT + 1)):
            self.cli_code("codex", "from pathlib import Path\nimport sys\n"
                          "Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text("
                          + repr(result) + ")")
            with self.assertRaises(worker.WorkerError):
                worker.run_agent("style", {}, "codex")

    def test_inventory_and_selection_without_launching_a_cli(self):
        self.assertEqual(worker.cli_inventory(), {"providers":[{"id":"claude","installed":False},
                                                              {"id":"codex","installed":False}]})
        self.assertEqual(worker.default_provider(), "claude")
        self.cli_code("codex", "raise SystemExit(1)")
        self.assertEqual(worker.default_provider(), "codex")
        self.cli_code("claude", "raise SystemExit(1)")
        self.assertEqual(worker.default_provider(), "claude")
        self.assertTrue(all(item["installed"] for item in worker.cli_inventory()["providers"]))
        with self.assertRaisesRegex(worker.WorkerError, "Choose Claude Code or Codex"):
            worker.run_agent("style", {}, "/usr/bin/false")
        (self.bin / "claude").unlink()
        with self.assertRaisesRegex(worker.WorkerError, "missing"):
            worker.run_agent("style", {}, "claude")

    def test_keychain_only_codex_setup_reports_how_to_sign_in(self):
        self.cli_code("codex", "raise SystemExit(9)")
        (self.codex_home / "auth.json").unlink()
        with patch("worker.subprocess.Popen") as process, self.assertRaisesRegex(worker.WorkerError, "file-backed"):
            worker.run_agent("style", {}, "codex")
        process.assert_not_called()

    def test_limits_and_independent_steps(self):
        base = {"outcome": "question", "text": "When is it due?", "kind": "decision", "commitments": []}
        step = {"title": "Review notes", "body": "Summarize supplied notes."}
        self.assertEqual(
            worker._validate("task", dict(base, independent_steps=[step]))["independent_steps"], [step]
        )
        for invalid in ("text", [step] * 7, [{"title": "Missing description"}]):
            with self.assertRaises(worker.WorkerError):
                worker._validate("task", dict(base, independent_steps=invalid))
        self.cli_code("claude", "raise SystemExit(9)")
        with self.assertRaisesRegex(worker.WorkerError, "exceeds the worker input limit"):
            worker.run_agent("task", {"body": "x" * worker.MAX_INPUT}, "claude")
        with self.assertRaisesRegex(worker.WorkerError, "encoded as JSON"):
            worker.run_agent("task", {"due": float("nan")}, "claude")
        with self.assertRaises(worker.WorkerError):
            worker._validate("style", {"body": "", "question": 7})


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()

        def git(*args):
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "commit.gpgsign=false",
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "-C",
                    str(self.repo),
                    *args,
                ],
                check=True,
                capture_output=True,
            )

        git("init", "-q")
        (self.repo / "example.txt").write_text("initial")
        git("add", "example.txt")
        git("commit", "-qm", "fixture")
        (self.repo / "example.txt").write_text("user changes")
        self.storage = self.root / "worktrees"
        self.first = worker.prepare_worktree(self.repo, self.storage, 1)

    def test_distinct_worktrees_and_retry(self):
        second = worker.prepare_worktree(self.repo, self.storage, 2)
        files = [{"path": "example.txt", "content": "change"}, {"path": "lib/new.py", "content": "pass\n"}]
        self.assertEqual(worker.apply_worktree_files(self.first, files), ["example.txt", "lib/new.py"])
        resumed = worker.prepare_worktree(self.repo, self.storage, 1)
        self.assertEqual(
            worker.worktree_context(resumed), {"lib/new.py": "pass\n", "example.txt": "change"}
        )
        self.assertEqual(worker.worktree_context(second), {"example.txt": "initial"})
        self.assertEqual((self.repo / "example.txt").read_text(), "user changes")
        with self.assertRaises(worker.WorkerError):
            worker.apply_worktree_files(self.repo, files)
        with self.assertRaises(worker.WorkerError):
            worker.prepare_worktree(self.repo, self.repo / "nested-worktrees", 3)

    def test_rejects_escape_metadata_and_special_files(self):
        outside = self.root / "outside"
        outside.write_text("untouched")
        (self.first / "leaf").symlink_to(outside)
        (self.first / "directory").symlink_to(self.root)
        os.link(outside, self.first / "alias")
        for path in (
            "leaf",
            "directory/outside",
            "../outside",
            "/tmp/example",
            ".git/config",
            "credentials.json",
            "alias",
            "cert.key",
            "lib//foo",
        ):
            with self.subTest(path=path), self.assertRaises(worker.WorkerError):
                worker.apply_worktree_files(
                    self.first,
                    [
                        {"path": "example.txt", "content": "should stay initial"},
                        {"path": path, "content": "bad"},
                    ],
                )
        self.assertEqual((self.first / "example.txt").read_text(), "initial")
        self.assertEqual(outside.read_text(), "untouched")
        (self.first / "token.pem").write_text("fixture")
        (self.first / "key.txt").write_text("-----BEGIN OPENSSH PRIVATE KEY-----")
        (self.first / "tool.bin").write_bytes(b"\0")
        self.assertEqual(worker.worktree_context(self.first), {"example.txt": "initial"})

    def test_refuses_unsupplied_and_stale_originals(self):
        before = worker.worktree_context(self.first)
        edits = [{"path": "example.txt", "content": "agent change"}]
        with self.assertRaises(worker.WorkerError):
            worker.apply_worktree_files(self.first, edits, {})
        (self.first / "example.txt").write_text("edited while model ran")
        with self.assertRaises(worker.WorkerError):
            worker.apply_worktree_files(self.first, edits, before)
        self.assertEqual((self.first / "example.txt").read_text(), "edited while model ran")
        (self.first / "example.txt").unlink()
        with self.assertRaises(worker.WorkerError):
            worker.apply_worktree_files(self.first, edits, before)
        self.assertFalse((self.first / "example.txt").exists())
