"""The app's own Hermes install, offline: a stand-in download serves a fake uv and Hermes source."""

import hashlib
import io
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import agents
import hermes_runtime
from hermes_runtime import InstallError, Runtime

FAKE_UV = f"""#!/bin/sh
# Stand-in for `uv sync`: creates the project environment with a hermes entry point.
[ "$1" = sync ] || exit 2
case " $* " in *" --frozen "*) ;; *) exit 3 ;; esac
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
printf '#!/bin/sh\\necho "Hermes Agent v{{version}} home=$HERMES_HOME"\\n' > "$UV_PROJECT_ENVIRONMENT/bin/hermes"
chmod +x "$UV_PROJECT_ENVIRONMENT/bin/hermes"
"""


def tar_bytes(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, (content, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), mode
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        home = patch.dict(os.environ, {"HOME": str(self.root / "home")})
        home.start()
        self.addCleanup(home.stop)
        self.version = hermes_runtime.HERMES_VERSION
        self.downloads = []

    def fetch(self, url, destination, sha256):
        self.downloads.append(url)
        if "astral-sh/uv" in url:
            target = url.rsplit("uv-", 1)[1].removesuffix(".tar.gz")
            body = tar_bytes({f"uv-{target}/uv": (FAKE_UV.replace("{version}", self.version).encode(), 0o755)})
        else:
            body = tar_bytes({"hermes-agent-2026.5.7/uv.lock": (b"lock", 0o644),
                              "hermes-agent-2026.5.7/web/index.html": (b"<html>", 0o644)})
        Path(destination).write_bytes(body)

    def test_installs_privately_and_writes_the_direct_command(self):
        (self.root / "home" / ".hermes").mkdir(parents=True)
        runtime = Runtime(self.data, self.fetch)
        runtime.ensure()
        self.assertTrue(runtime.installed(), runtime.state())
        self.assertEqual(runtime.state()["status"], "ready")
        self.assertEqual(Path(runtime.binary()).resolve().parent.parent.parent, runtime.source.resolve())
        script = runtime.wrapper.read_text()
        self.assertIn("HERMES_HOME=" + str(self.data / "hermes"), script)
        self.assertNotIn(str(self.root / "home" / ".hermes"), script)
        link = self.root / "home" / ".local" / "bin" / "memyselfai-hermes"
        self.assertEqual(link.resolve(), runtime.wrapper.resolve())
        self.assertEqual(list((self.root / "home" / ".hermes").iterdir()), [])
        # Already installed: a second start downloads nothing.
        count = len(self.downloads)
        runtime.ensure()
        self.assertEqual(len(self.downloads), count)

    def test_a_failed_update_keeps_the_previous_version(self):
        runtime = Runtime(self.data, self.fetch)
        runtime.ensure()
        working = runtime.binary()
        self.version = "0.0.1"  # The new download reports the wrong version.
        with patch.object(hermes_runtime, "HERMES_VERSION", "9.9.9"), patch.object(hermes_runtime, "HERMES_TAG", "v9"):
            runtime.ensure()
            self.assertEqual(runtime.state()["status"], "failed")
            self.assertIn("reported", runtime.state()["error"])
        self.assertEqual(runtime.binary(), working)

    def test_failures_are_recorded_for_retry(self):
        def offline(url, destination, sha256):
            raise InstallError("Could not download: offline")

        runtime = Runtime(self.data, offline)
        runtime.ensure()
        self.assertIsNone(runtime.binary())
        self.assertEqual(runtime.state()["status"], "failed")
        self.assertIn("offline", runtime.state()["error"])

    def test_download_rejects_a_wrong_checksum(self):
        source = self.root / "payload"
        source.write_bytes(b"tampered")
        target = self.root / "out"
        with self.assertRaisesRegex(InstallError, "checksum"):
            hermes_runtime.download(source.as_uri(), target, hashlib.sha256(b"original").hexdigest())
        hermes_runtime.download(source.as_uri(), target, hashlib.sha256(b"tampered").hexdigest())

    def test_extract_refuses_paths_outside_the_folder(self):
        archive = self.root / "evil.tar.gz"
        archive.write_bytes(tar_bytes({"../escape": (b"x", 0o644)}))
        with self.assertRaises(InstallError):
            hermes_runtime.extract(archive, self.root / "out")
        self.assertFalse((self.root / "escape").exists())

    def test_the_app_never_uses_another_hermes(self):
        other = self.root / "bin" / "hermes"
        other.parent.mkdir()
        other.write_text("#!/bin/sh\n")
        other.chmod(other.stat().st_mode | stat.S_IXUSR)
        with patch.dict(os.environ, {"PATH": str(other.parent) + os.pathsep + os.environ["PATH"]}):
            self.assertIsNone(agents.executable("hermes", self.data))
            self.assertIsNone(agents.executable("hermes"))
            Runtime(self.data, self.fetch).ensure()
            self.assertTrue(agents.executable("hermes", self.data).startswith(str(self.data)))


if __name__ == "__main__":
    unittest.main()
