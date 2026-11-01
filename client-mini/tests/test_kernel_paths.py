import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from client_mini.config import MiniConfig
from client_mini.kernal_copy import copy_kernels
from client_mini.kernel_state import _lock_dir
from client_mini.launcher import exec_bin, get_kernel_path


class KernelPathsTests(unittest.TestCase):
    def test_independent_paths_and_launch_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "client.json").write_text('{"settings":{"api_key":"test"}}')
            config = root / "mini.json"
            config.write_text(json.dumps({
                "client_config": "client.json", "data_dir": "data", "code_dir": "common",
                "fuel_code_dir": "fuel-root", "fusion_code_dir": "fusion-root",
                "aqua_code_dir": "aqua-root", "rocket_code_dir": "rocket-root",
                "scm_code_dir": "scm-root", "auth": {"refresh_token": "test"},
            }))
            cfg = MiniConfig(config)
            tokens = Mock()
            tokens.get_access_token.return_value = "test"
            user = {"is_logged_in": True, "permissions": []}
            with patch("client_mini.launcher.IS_WINDOWS", True), \
                    patch("client_mini.launcher.is_kernel_running", return_value=False), \
                    patch("client_mini.launcher.check_permission", return_value=True), \
                    patch("client_mini.launcher.process_manager.spawn_process") as spawn:
                for kernel in ("fuel", "fusion", "aqua", "rocket", "scm"):
                    directory = root / f"{kernel}-root"
                    binary = directory / kernel / f"{kernel}.exe"
                    binary.parent.mkdir(parents=True)
                    binary.touch()
                    self.assertEqual(cfg.get_kernel_code_dir(kernel), directory)
                    self.assertEqual(get_kernel_path(cfg, kernel), binary)
                    with patch("client_mini.launcher.get_select_kernel", return_value=kernel):
                        exec_bin(cfg, tokens, user, ["run"], "test", kernel)
                    self.assertEqual(spawn.call_args.args[0], binary)
                    env = spawn.call_args.args[2]
                    self.assertEqual(env["FUEL_CODE_PATH"], str(directory))
                    self.assertEqual(env["PYTHONPATH"], str(directory))
            self.assertEqual(_lock_dir(cfg, "fuel"), root / "fuel-root" / "data")
            self.assertEqual(_lock_dir(cfg, "scm"), root / "scm-root" / "scm" / "data")

    def test_fallback_and_sync_individual_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "client.json").write_text("{}")
            config = root / "mini.json"
            config.write_text(json.dumps({
                "client_config": "client.json", "data_dir": "data",
                "fuel_code_dir": str(root / "fuel-only"), "rocket_code_dir": "",
                "auth": {"refresh_token": "test"},
            }))
            cfg = MiniConfig(config)
            self.assertEqual(cfg.get_kernel_code_dir("rocket"), cfg.code_dir)
            self.assertEqual(cfg.get_kernel_code_dir("fusion"), cfg.code_dir)
            source = cfg.default_code_dir
            (source / "fuel").mkdir(parents=True)
            (source / "fuel" / "fuel.exe").touch()
            (source / "fuel_v1.yml").write_text("v1")
            with patch("client_mini.kernal_copy.IS_WINDOWS", True), \
                    patch("client_mini.kernal_copy.is_kernel_busy", return_value=False):
                self.assertEqual(copy_kernels(cfg), ["fuel"])
            self.assertTrue((root / "fuel-only" / "fuel" / "fuel.exe").exists())
            self.assertTrue((source / "fuel" / "fuel.exe").exists())


if __name__ == "__main__":
    unittest.main()
