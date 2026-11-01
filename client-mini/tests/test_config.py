import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from client_mini.config import MiniConfig


class MiniConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.client = self.root / "client" / "config.json"
        self.client.parent.mkdir()
        self.original = json.dumps({
            "settings": {"all_data_path": "./data"},
            "auto_real_trading": True,
        })
        self.client.write_text(self.original, encoding="utf-8")
        self.mini = self.root / "mini.json"
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def load(self, **overrides):
        data = {"client_config": "client/config.json", "auth": {"refresh_token": "test"}}
        data.update(overrides)
        self.mini.write_text(json.dumps(data), encoding="utf-8")
        return MiniConfig(self.mini)

    def test_defaults_and_read_only_client(self):
        rebalance = {"test": "09:30"}
        (self.client.parent / "rebalance_time.json").write_text(json.dumps(rebalance))
        cfg = self.load()
        self.assertEqual(cfg.path, self.client)
        self.assertEqual(cfg.data_dir, self.client.parent / "data")
        self.assertEqual(cfg.code_dir, cfg.data_dir / "code")
        self.assertEqual(cfg.state_dir, self.root / "state")
        self.assertTrue(cfg.state_dir.is_dir())
        self.assertEqual(cfg.data["rebalance_time"], rebalance)
        self.assertTrue(cfg.get_switch("auto_real_trading"))
        self.assertEqual(self.client.read_text(encoding="utf-8"), self.original)

    def test_overrides_without_client_data_path(self):
        self.client.write_text("{}")
        with patch.dict(os.environ, {"CODE_DATA_PATH": "env-code"}):
            cfg = self.load(data_dir="data", code_dir="code", state_dir="runtime",
                            auto_real_trading=False)
        self.assertEqual(cfg.data_dir, self.root / "data")
        self.assertEqual(cfg.code_dir, self.root / "code")
        self.assertEqual(cfg.default_code_dir, cfg.data_dir / "code")
        self.assertEqual(cfg.state_dir, self.root / "runtime")
        self.assertEqual(cfg.path, self.client)
        self.assertFalse(cfg.get_switch("auto_real_trading"))

    def test_environment_ignored_and_empty_path_fallbacks(self):
        with patch.dict(os.environ, {"CODE_DATA_PATH": "env-code"}):
            cfg = self.load(data_dir="", code_dir="", state_dir="")
        self.assertEqual(cfg.code_dir, cfg.data_dir / "code")
        self.assertEqual(cfg.data_dir, self.client.parent / "data")
        self.assertEqual(cfg.state_dir, self.root / "state")

    def test_absolute_paths(self):
        cfg = self.load(data_dir=str(self.root / "absolute-data"),
                        code_dir=str(self.root / "absolute-code"))
        self.assertEqual(cfg.data_dir, self.root / "absolute-data")
        self.assertEqual(cfg.code_dir, self.root / "absolute-code")

    def test_missing_client_config_rejected(self):
        with self.assertRaisesRegex(ValueError, "client_config"):
            self.load(client_config="", data_dir="data", code_dir="code")

    def test_missing_data_path_rejected(self):
        self.client.write_text("{}")
        with self.assertRaisesRegex(ValueError, "data_dir"):
            self.load()

    def test_credentials_fallback_uses_client_directory(self):
        with patch("client_mini.safe_storage.read_refresh_token", return_value="decrypted") as read:
            cfg = self.load(auth={})
        read.assert_called_once_with(self.client.parent)
        self.assertEqual(cfg.refresh_token, "decrypted")


if __name__ == "__main__":
    unittest.main()
