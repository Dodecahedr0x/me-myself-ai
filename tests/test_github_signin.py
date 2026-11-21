import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import github_signin as signin


class GitHubSignInTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.cli = self.root / "gh"
        self.cli.write_text(
            f"#!{sys.executable}\n"
            + """
import json, os, pathlib, subprocess, sys, time
root = pathlib.Path(__file__).parent
args = sys.argv[1:]
cfg = pathlib.Path(os.environ["GH_CONFIG_DIR"])
with (root / "calls").open("a") as f:
    f.write(json.dumps([args, str(cfg), "GH_TOKEN" in os.environ, "GITHUB_TOKEN" in os.environ]) + "\\n")
if args[:2] == ["auth", "token"]:
    if (cfg / "session").exists():
        print((cfg / "session").read_text())
    else:
        sys.exit(1)
elif args[:2] == ["auth", "login"]:
    assert {"--web", "--hostname", "github.com"} <= set(args) and "--insecure-storage" not in args
    print("do not show fixture_private or https://example.invalid/login/device", file=sys.stderr)
    print("! First copy your one-time code: AB12-CD34", file=sys.stderr, flush=True)
    print("Open this URL to continue in your web browser: https://github.com/login/device", file=sys.stderr)
    (cfg / "session").write_text("fixture_browser")
    if (root / "wait").exists():
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        (root / "child").write_text(str(child.pid))
        time.sleep(60)
elif args[0] == "api":
    assert "GH_TOKEN" not in os.environ
    assert "--method" in args and "GET" in args
    if "user" in args:
        print("Test-User")
    else:
        assert "--paginate" in args and any("organization_member" in arg for arg in args)
        print("Test-User/project\\norg/private-repo\\nTest-User/project")
        if (root / "failed_page").exists():
            print("fixture_private", file=sys.stderr)
            sys.exit(1)
else:
    sys.exit(1)
"""
        )
        self.cli.chmod(0o700)
        self.config = self.root / "regular"
        self.config.mkdir()
        self.env = patch.dict(
            os.environ,
            {"GH_CONFIG_DIR": str(self.config), "GH_TOKEN": "fixture_ci", "GITHUB_TOKEN": "fixture_ci"},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        lookup = patch.object(signin.shutil, "which", return_value=str(self.cli))
        lookup.start()
        self.addCleanup(lookup.stop)
        self.cancel = threading.Event()
        self.progress = Mock()

    def calls(self):
        return [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]

    def test_existing_session_is_verified_without_listing_repositories(self):
        (self.config / "session").write_text("fixture_cli")
        result = signin.authorize(self.cancel, self.progress)
        self.assertEqual(result, {"account": "Test-User", "options": []})
        calls = self.calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2:], [False, False])
        self.assertNotIn("fixture_cli", str(calls))
        self.assertEqual(signin.token_for("Test-User"), "fixture_cli")
        self.assertEqual(self.calls()[-1][0][-2:], ["--user", "Test-User"])

    def test_browser_flow_configures_gh_and_exposes_only_device_code(self):
        result = signin.authorize(self.cancel, self.progress)
        self.assertEqual(result["account"], "Test-User")
        display = [call.kwargs for call in self.progress.call_args_list]
        device = [item for item in display if "code" in item]
        self.assertEqual([(x["url"], x["code"]) for x in device], [(signin.DEVICE_URL, "AB12-CD34")])
        self.assertNotIn("example.invalid", str(display))
        self.assertEqual((self.config / "session").read_text(), "fixture_browser")
        self.assertEqual(self.calls()[1][1], str(self.config))

    def test_cancel_terminates_login_and_children(self):
        (self.root / "wait").touch()
        self.progress.side_effect = lambda **v: self.cancel.set() if v.get("code") else None
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            signin.authorize(self.cancel, self.progress)
        child = int((self.root / "child").read_text())
        for _ in range(50):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            self.fail("CLI child still running after cancellation")

    def test_token_lookup_fails_without_logging_in(self):
        with self.assertRaisesRegex(RuntimeError, "gh auth login"):
            signin.token_for("Test-User")
        self.assertEqual(len(self.calls()), 1)

    def test_missing_cli_offers_manual_setup(self):
        with patch.object(signin.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install GitHub CLI.*manual setup"):
                signin.authorize(self.cancel, self.progress)

    def test_timeout(self):
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            signin._run(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                {},
                self.cancel,
                time.monotonic() + 0.05,
            )
