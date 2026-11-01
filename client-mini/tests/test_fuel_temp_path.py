import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from client_mini.config import MiniConfig
from client_mini.launcher import exec_bin


class FuelTempPathTests(unittest.TestCase):
    def test_config_and_spawn_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            client = root / "client.json"
            client.write_text("{}")
            config = root / "mini.json"
            for value in (None, "", "temp", str(root / "absolute-temp")):
                data = {"client_config": str(client), "data_dir": "data",
                        "auth": {"refresh_token": "test"}}
                if value is not None:
                    data["fuel_temp_file_path"] = value
                config.write_text(json.dumps(data))
                cfg = MiniConfig(config)
                expected = str((root / value).resolve()) if value else ""
                self.assertEqual(cfg.fuel_temp_file_path, expected)
                tokens = Mock()
                tokens.get_access_token.return_value = "test"
                with patch("client_mini.launcher.get_kernel_path", return_value=client), \
                        patch("client_mini.launcher.is_kernel_running", return_value=False), \
                        patch("client_mini.launcher.process_manager.spawn_process") as spawn:
                    exec_bin(cfg, tokens, {}, ["all_data"], "test")
                    self.assertEqual(spawn.call_args.args[2]["FUEL_TEMP_FILE_PATH"], expected)
                    for override in ("explicit", "", {"FUEL_TEMP_FILE_PATH": "dict-value"}):
                        exec_bin(cfg, tokens, {}, ["all_data"], "test", extra_env=override)
                        result = override if isinstance(override, str) else "dict-value"
                        self.assertEqual(spawn.call_args.args[2]["FUEL_TEMP_FILE_PATH"], result)
                    with patch("client_mini.launcher.check_permission", return_value=True):
                        # Supply identity so the non-fuel permission gate passes.
                        cfg.data["settings"] = {"api_key": "test"}
                        spawn.reset_mock()
                        exec_bin(cfg, tokens, {"is_logged_in": True}, ["run"], "test", "rocket")
                        self.assertEqual(spawn.call_args.args[2]["FUEL_TEMP_FILE_PATH"], "")


if __name__ == "__main__":
    unittest.main()
