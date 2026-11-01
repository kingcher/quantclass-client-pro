import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from client_mini.kernal_copy import copy_kernels


class KernalCopyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.source = root / "data" / "code"
        self.target = root / "custom"
        self.cfg = SimpleNamespace(default_code_dir=self.source, code_dir=self.target,
                                   bootstrap={"code_dir": str(self.target)})
        busy = patch("client_mini.kernal_copy.is_kernel_busy", return_value=False)
        self.busy = busy.start()
        self.addCleanup(busy.stop)
        platform = patch("client_mini.kernal_copy.IS_WINDOWS", True)
        platform.start()
        self.addCleanup(platform.stop)

    def install(self, directory, version, content="new"):
        (directory / "fuel").mkdir(parents=True, exist_ok=True)
        (directory / "fuel" / "fuel.exe").write_text(content)
        (directory / f"fuel_{version}.yml").write_text(version)

    def test_changed_version_replaces_kernel_and_markers_only(self):
        self.install(self.source, "v2")
        self.install(self.target, "v1", "old")
        (self.target / "fuel" / "obsolete.dll").touch()
        (self.target / "unrelated.txt").touch()
        self.assertEqual(copy_kernels(self.cfg), ["fuel"])
        self.assertEqual((self.target / "fuel" / "fuel.exe").read_text(), "new")
        self.assertFalse((self.target / "fuel" / "obsolete.dll").exists())
        self.assertFalse((self.target / "fuel_v1.yml").exists())
        self.assertTrue((self.target / "fuel_v2.yml").exists())
        self.assertTrue((self.target / "unrelated.txt").exists())
        self.assertTrue((self.source / "fuel_v2.yml").exists())

    def test_same_version_skipped(self):
        self.install(self.source, "v1")
        self.install(self.target, "v1", "unchanged")
        self.assertEqual(copy_kernels(self.cfg), [])
        self.assertEqual((self.target / "fuel" / "fuel.exe").read_text(), "unchanged")
        self.busy.assert_not_called()

    def test_missing_target_copied(self):
        self.install(self.source, "v1")
        self.assertEqual(copy_kernels(self.cfg), ["fuel"])

    def test_matching_marker_but_missing_binary_repaired(self):
        self.install(self.source, "v1")
        self.install(self.target, "v1")
        (self.target / "fuel" / "fuel.exe").unlink()
        self.assertEqual(copy_kernels(self.cfg), ["fuel"])

    def test_default_or_child_directory_skipped(self):
        for target in [self.source, self.source / "child"]:
            self.cfg.code_dir = target
            self.assertEqual(copy_kernels(self.cfg), [])
        self.cfg.code_dir = self.target
        self.cfg.bootstrap = {}
        self.assertEqual(copy_kernels(self.cfg), [])

    def test_missing_source_preserves_target(self):
        self.install(self.target, "v1")
        self.assertEqual(copy_kernels(self.cfg), [])
        self.assertTrue((self.target / "fuel_v1.yml").exists())

    def test_ambiguous_source_rejected(self):
        self.install(self.source, "v1")
        self.install(self.source, "v2")
        with self.assertRaises(ValueError):
            copy_kernels(self.cfg)
        self.assertFalse(self.target.exists())

    def test_busy_kernel_rejected(self):
        self.install(self.source, "v1")
        self.busy.return_value = True
        with self.assertRaises(RuntimeError):
            copy_kernels(self.cfg)
        self.assertFalse(self.target.exists())

    def test_copy_failure_preserves_existing_kernel(self):
        self.install(self.source, "v2")
        self.install(self.target, "v1", "old")
        with patch("client_mini.kernal_copy.shutil.copytree", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                copy_kernels(self.cfg)
        self.assertEqual((self.target / "fuel" / "fuel.exe").read_text(), "old")
        self.assertTrue((self.target / "fuel_v1.yml").exists())


if __name__ == "__main__":
    unittest.main()
