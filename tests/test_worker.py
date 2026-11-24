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
        self.workdir = (self.root / "task checkout").resolve()
        self.workdir.mkdir()
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
assert Path.cwd().name == 'task checkout' and os.listdir() == ['fixture.txt']
assert not {'GITHUB_TOKEN', 'CUSTOM_SECRET', 'ANTHROPIC_BASE_URL', 'OPENAI_BASE_URL'} & os.environ.keys()
instructions, context = sys.stdin.read().split('Context JSON:', 1)
assert 'name: task-interaction' in instructions
payload = json.loads(context)
assert payload['source'] == 'fictional fixture' and payload['working_directory'] == os.getcwd()
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
        (self.workdir / "fixture.txt").write_text("fixture")
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "test", "CODEX_API_KEY": "test",
                                     "ANTHROPIC_BASE_URL": "wrong", "OPENAI_BASE_URL": "wrong"}):
            for provider in worker.PROVIDERS:
                self.cli_code(provider, script)
                with self.subTest(provider=provider):
                    results = [worker.run_agent("task", {"source": "fictional fixture"}, provider, self.workdir)
                               for _ in range(2)]
                    self.assertEqual([r["text"] for r in results], [str(self.workdir)] * 2)
                    self.assertNotIn("files", results[0])

    def test_retryable_failures(self):
        for provider in worker.PROVIDERS:
            for script in ("raise SystemExit(2)", "print('invalid')", "print('[]')"):
                self.cli_code(provider, script)
                with self.subTest(provider=provider, script=script), self.assertRaises(worker.WorkerError):
                    worker.run_agent("discovery", {}, provider, self.workdir)
            self.cli_code(provider, "import time; time.sleep(10)")
            with patch("worker.AGENT_TIMEOUT", 0.05), self.assertRaisesRegex(worker.WorkerError, "timed out"):
                worker.run_agent("task", {}, provider, self.workdir)

    def test_error_envelopes_missing_fields_and_invalid_proposals(self):
        for envelope in (
            {"type":"result", "subtype":"error_during_execution", "is_error":True,
             "structured_output":{"body":"bad"}},
            {"type":"result", "subtype":"success", "is_error":False, "result":"ambiguous raw output"},
            {"type":"result", "subtype":"success", "is_error":False, "structured_output":{"body":5}},
        ):
            self.cli_code("claude", "print(" + repr(json.dumps(envelope)) + ")")
            with self.assertRaises(worker.WorkerError):
                worker.run_agent("style", {}, "claude", self.workdir)
        for result in ('{"body": 15}', '{"notBody":"wrong"}', 'not-json', 'x' * (worker.MAX_OUTPUT + 1)):
            self.cli_code("codex", "from pathlib import Path\nimport sys\n"
                          "Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text("
                          + repr(result) + ")")
            with self.assertRaises(worker.WorkerError):
                worker.run_agent("style", {}, "codex", self.workdir)

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
            worker.run_agent("style", {}, "/usr/bin/false", self.workdir)
        (self.bin / "claude").unlink()
        with self.assertRaisesRegex(worker.WorkerError, "missing"):
            worker.run_agent("style", {}, "claude", self.workdir)

    def test_keychain_only_codex_setup_reports_how_to_sign_in(self):
        self.cli_code("codex", "raise SystemExit(9)")
        (self.codex_home / "auth.json").unlink()
        with patch("worker.subprocess.Popen") as process, self.assertRaisesRegex(worker.WorkerError, "file-backed"):
            worker.run_agent("style", {}, "codex", self.workdir)
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
            worker.run_agent("task", {"body": "x" * worker.MAX_INPUT}, "claude", self.workdir)
        with self.assertRaisesRegex(worker.WorkerError, "encoded as JSON"):
            worker.run_agent("task", {"due": float("nan")}, "claude", self.workdir)
        with self.assertRaises(worker.WorkerError):
            worker._validate("style", {"body": "", "question": 7})

    def test_decision_keeps_long_context_out_of_shortcuts(self):
        source = "Which release?\nThe source lists two upcoming milestones."
        pick = dict(label="May", detail="Cover the May milestone.")
        result = worker._validate("decision", dict(question=source, context="The changelog needs a cutoff.",
                                  choices=[pick, dict(pick, label="MAY"), dict(pick, label="x"*33)]))
        self.assertEqual(result["question"], "Which release?")
        self.assertIn("two upcoming milestones", result["context"])
        self.assertEqual(result["choices"], [pick])


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

    @unittest.skipUnless(sys.platform == "darwin", "macOS command sandbox")
    def test_command_runs_with_private_files_and_without_external_access(self):
        (self.root / "private-note").write_text("private fixture")
        (self.first / "outside").symlink_to(self.root / "private-note")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture"}):
            result = worker.run_command(self.first, """
                set -e
                test -z "$GITHUB_TOKEN"
                printf 'ran' > result.txt
                if cat outside 2>/dev/null; then exit 40; fi
                if echo changed >> .git/config 2>/dev/null; then exit 41; fi
                if python3 -c 'import socket; socket.socket().connect(("127.0.0.1", 1))' 2> "$TMPDIR/net"; then exit 42; fi
                grep -Eiq 'not permitted|denied' "$TMPDIR/net"
                python3 -c 'print("fixture result", 1 + 2)'
                exit 7
                """)
        self.assertEqual(result["exit_code"], 7, result["output"])
        self.assertIn("fixture result 3", result["output"])
        self.assertEqual((self.first / "result.txt").read_text(), "ran")

    def test_task_owns_git_objects_and_can_resume_without_source(self):
        self.assertEqual(worker._location(self.first, "--git-common-dir"), self.first / ".git")
        self.assertEqual(worker._git(self.repo, "branch", "--list", "memyselfai/*"), b"")
        (self.first / "result.txt").write_text("fixture result")
        self.repo.rename(self.root / "moved")
        resumed = worker.prepare_worktree(self.repo, self.storage, 1)
        self.assertEqual((resumed / "result.txt").read_text(), "fixture result")

    def test_legacy_tree_keeps_uncommitted_files_and_deletions(self):
        legacy = self.storage / "task-old"
        worker._git(self.repo, "worktree", "add", "-b", "memyselfai/task-old", str(legacy))
        (legacy / "example.txt").unlink()
        (legacy / "notes").write_text("staged")
        worker._git(legacy, "add", "notes")
        (legacy / "notes").write_text("saved work")
        migrated = worker.resume_worktree(legacy, self.storage, "old")
        self.assertNotEqual(migrated, legacy)
        self.assertFalse((migrated / "example.txt").exists())
        self.assertEqual((migrated / "notes").read_text(), "saved work")
        self.assertEqual(worker._git(migrated, "show", ":notes"), b"staged")
        self.assertEqual(worker._location(migrated, "--git-common-dir"), migrated / ".git")

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

    def test_project_dotfiles_are_editable(self):
        files = [
            {"path": ".github/workflows/ci.yml", "content": "on: pull_request\n"},
            {"path": ".gitignore", "content": "build/\n"},
        ]
        self.assertEqual(
            worker.apply_worktree_files(self.first, files), [".github/workflows/ci.yml", ".gitignore"]
        )
        self.assertEqual((self.first / ".github/workflows/ci.yml").read_text(), "on: pull_request\n")
        context = worker.worktree_context(self.first)
        self.assertEqual(context[".github/workflows/ci.yml"], "on: pull_request\n")
        self.assertEqual(context[".gitignore"], "build/\n")
        (self.first / ".env").write_text("API=fixture")
        (self.first / ".npmrc").write_text("registry=fixture")
        self.assertNotIn(".env", worker.worktree_context(self.first))
        self.assertNotIn(".npmrc", worker.worktree_context(self.first))

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
            ".GIT/config",
            ".github/../.git/config",
            ".env",
            ".npmrc",
            ".ssh/config",
            ".github/workflows/.secrets",
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

    def test_larger_named_source_can_be_edited_but_oversized_source_is_reported(self):
        source = "x" * 32000
        (self.first / "source.py").write_text(source)
        (self.first / "too_big.py").write_text("x" * 110000)
        self.assertNotIn("source.py", worker.worktree_context(self.first))
        omitted = {}
        files = worker.worktree_context(self.first, "Read source.py and too_big.py", omitted)
        self.assertIn("too_big.py", omitted)
        self.assertEqual(files["source.py"], source)
        worker.apply_worktree_files(self.first, [dict(path="source.py", content=source + "y")], files)
        self.assertEqual((self.first / "source.py").read_text(), source + "y")

    def test_source_with_header_only_fixture_and_detector_is_available(self):
        header = "-----BEGIN OPENSSH PRIVATE KEY-----"
        source = f'path.write_text("{header}")\n' + r'pattern = rb"-----BEGIN [^\n]*PRIVATE KEY-----"'
        (self.first / "example.py").write_text(source)
        for name, content in {
            "standalone.txt": header, "embedded.py": f"key = '''{header}\nfixture\n'''",
            "escaped.py": f'key = "{header}\\nfixture"', "binary.py": "source\x00",
            "split.py": f'header = "{header}"\nbody = "' + "A" * 64 + '"',
        }.items():
            (self.first / name).write_text(content)
        omissions = {}
        context = worker.worktree_context(self.first, "example.py standalone.txt embedded.py escaped.py split.py binary.py", omissions)
        self.assertEqual(context["example.py"], source)
        self.assertEqual(set(omissions), {"standalone.txt", "embedded.py", "escaped.py", "split.py", "binary.py"})
        self.assertIn("private", omissions["embedded.py"].lower())
        self.assertIn("binary", omissions["binary.py"].lower())

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
