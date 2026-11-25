"""The desktop service runs from a copy that outlives the app build. See docs/specs/desktop-runtime.md."""

import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "launch_service", Path(__file__).resolve().parent.parent / "desktop" / "launch_service.py")
launch_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch_service)


class StableResourcesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"

    def bundle(self, name, seal):
        contents = self.root / name / "MeMyselfAI.app" / "Contents"
        (contents / "Resources" / "skills").mkdir(parents=True)
        (contents / "Resources" / "skills" / "SKILL.md").write_text("skill " + seal)
        (contents / "Helpers").mkdir()
        (contents / "Helpers" / "workspace-node").write_text("node")
        (contents / "_CodeSignature").mkdir()
        (contents / "_CodeSignature" / "CodeResources").write_text(seal)
        return contents / "Resources"

    def test_copy_survives_the_build_being_deleted(self):
        resources = self.bundle("build1", "one")
        stable = launch_service.stable_resources(resources, str(self.data))
        shutil.rmtree(self.root / "build1")
        self.assertEqual((stable / "skills" / "SKILL.md").read_text(), "skill one")
        self.assertEqual((stable.parent / "Helpers" / "workspace-node").read_text(), "node")

    def test_a_new_build_replaces_the_old_copy(self):
        first = launch_service.stable_resources(self.bundle("build1", "one"), str(self.data))
        self.assertEqual(launch_service.stable_resources(self.bundle("build1b", "one"), str(self.data)), first)
        second = launch_service.stable_resources(self.bundle("build2", "two"), str(self.data))
        self.assertNotEqual(first, second)
        self.assertFalse(first.exists())
        self.assertEqual((second / "skills" / "SKILL.md").read_text(), "skill two")

    def test_unsigned_folders_run_in_place(self):
        resources = self.root / "checkout"
        resources.mkdir()
        self.assertEqual(launch_service.stable_resources(resources, str(self.data)), resources)

    def test_data_argument(self):
        self.assertEqual(launch_service.data_argument(["x", "--data", "/d"]), "/d")
        self.assertEqual(launch_service.data_argument(["x", "--data=/e"]), "/e")
        self.assertIsNone(launch_service.data_argument(["x"]))
