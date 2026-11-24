"""Toolchains and dependency fetches. See "Toolchains and dependencies" in docs/specs/local-agent.md."""

import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import toolchains
import worker


def go_archive(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class GoInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "toolchains"
        goos, _ = toolchains._os()
        goarch, _ = toolchains._arch()
        self.filename = f"go1.99.1.{goos}-{goarch}.tar.gz"
        self.archive = go_archive({"go/bin/go": "#!/bin/sh\necho go version go1.99.1\n", "go/VERSION": "go1.99.1"})
        self.index = [dict(version="go1.99.1", stable=True, files=[dict(
            filename=self.filename, os=goos, arch=goarch, kind="archive",
            sha256=hashlib.sha256(self.archive).hexdigest())])]

    def get(self, url, limit=0):
        if url == toolchains.GO_INDEX:
            return json.dumps(self.index).encode()
        self.assertEqual(url, toolchains.GO_DOWNLOAD + self.filename)
        return self.archive

    def test_installs_the_verified_release_and_points_go_at_it(self):
        detail = toolchains.install(self.root, "go", get=self.get)
        self.assertIn("go1.99.1", detail)
        self.assertTrue((self.root / "go" / "bin" / "go").is_file())
        self.assertEqual(os.readlink(self.root / "go"), "go1.99.1")
        self.assertIn(str(self.root / "go" / "bin"), toolchains.bin_paths(self.root))

    def test_rejects_a_checksum_mismatch_and_keeps_the_previous_toolchain(self):
        toolchains.install(self.root, "go", get=self.get)
        self.index[0].update(version="go1.99.2")
        self.archive = go_archive({"go/bin/go": "tampered"})
        with self.assertRaises(toolchains.SetupError):
            toolchains.install(self.root, "go", get=self.get)
        self.assertEqual(os.readlink(self.root / "go"), "go1.99.1")
        self.assertFalse((self.root / "go1.99.2").exists())

    def test_rejects_paths_outside_the_go_folder(self):
        self.archive = go_archive({"go/bin/go": "x", "evil/escape": "x"})
        self.index[0]["files"][0]["sha256"] = hashlib.sha256(self.archive).hexdigest()
        with self.assertRaises(toolchains.SetupError):
            toolchains.install(self.root, "go", get=self.get)
        self.assertFalse((self.root / "evil").exists())

    def test_downloads_only_from_official_hosts(self):
        with self.assertRaises(toolchains.SetupError):
            toolchains._get("https://example.com/go.tar.gz")


class RustInstallTests(unittest.TestCase):
    def test_refuses_a_rustup_with_the_wrong_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            def get(url, limit=0):
                return (b"0" * 64 + b"  *rustup-init") if url.endswith(".sha256") else b"binary"
            with patch.object(toolchains.subprocess, "run") as run:
                with self.assertRaises(toolchains.SetupError):
                    toolchains.install(Path(tmp), "rust", get=get)
            run.assert_not_called()


class SetupStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        (self.work / "svc").mkdir(parents=True)
        (self.work / "svc" / "go.mod").write_text("module example.com/svc\n")
        (self.work / "web").mkdir()
        (self.work / "web" / "package.json").write_text("{}")

    def test_parses_only_the_listed_steps(self):
        self.assertEqual(toolchains.parse("install go", self.work), ("install", "go", None))
        self.assertEqual(toolchains.parse("fetch go svc", self.work)[2], (self.work / "svc").resolve())
        for bad in ("install python", "fetch go ../..", "fetch go /etc", "fetch go web", "fetch pip svc",
                    "rm -rf /", "fetch npm .git"):
            with self.assertRaises(toolchains.SetupError, msg=bad):
                toolchains.parse(bad, self.work)

    def test_fetch_commands_are_fixed_and_disable_install_scripts(self):
        self.assertEqual(toolchains.fetch_command("go", self.work / "svc"), "go mod download")
        self.assertIn("npm install --ignore-scripts", toolchains.fetch_command("npm", self.work / "web"))
        (self.work / "web" / "package-lock.json").write_text("{}")
        command = toolchains.fetch_command("npm", self.work / "web")
        self.assertTrue(command.startswith("npm ci --ignore-scripts"))
        self.assertIn("--registry https://registry.npmjs.org/", command)

    def test_cache_env_points_go_and_cargo_at_the_task_cache(self):
        env = toolchains.cache_env(self.work / "tools", self.work / "cache")
        self.assertEqual(env["GOMODCACHE"], str(self.work / "cache" / "go" / "pkg" / "mod"))
        self.assertEqual(env["CARGO_HOME"], str(self.work / "cache" / "cargo"))
        self.assertIn("-modcacherw", env["GOFLAGS"])


@unittest.skipUnless(sys.platform == "darwin" or (sys.platform.startswith("linux") and worker.shutil.which("bwrap")),
                     "command sandbox")
class FetchNetworkTests(unittest.TestCase):
    """Only fixed fetches get network; an ordinary command after a fetch still has none."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name).resolve() / "work"
        self.work.mkdir()
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        self.tools = Path(self.tmp.name).resolve() / "toolchains"
        self.tools.mkdir()
        self.cache = Path(self.tmp.name).resolve() / "cache"
        try:
            socket.create_connection(("1.1.1.1", 443), 5).close()
        except OSError:
            self.skipTest("no network on this machine")

    def probe(self, network):
        script = "python3 -c 'import socket; socket.create_connection((\"1.1.1.1\", 443), 5); print(\"CONN\" + \"ECTED\")'"
        return worker.run_command(self.work, script, tools=self.tools, cache=self.cache, network=network)

    def test_network_only_for_fetches(self):
        self.assertIn("CONNECTED", self.probe(True)["output"])
        self.assertNotIn("CONNECTED", self.probe(False)["output"])
        self.assertTrue(self.cache.is_dir())


if __name__ == "__main__":
    unittest.main()
