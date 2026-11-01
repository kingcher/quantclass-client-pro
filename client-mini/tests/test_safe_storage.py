import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from client_mini import safe_storage


class SafeStorageTests(unittest.TestCase):
    def test_windows_v10(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nonce, ciphertext, tag = b"n" * 12, b"cipher", b"t" * 16
            (root / "refresh-token.enc").write_bytes(b"v10" + nonce + ciphertext + tag)
            (root / "Local State").write_text(json.dumps({"os_crypt": {
                "encrypted_key": base64.b64encode(b"DPAPIwrapped-key").decode()
            }}))
            with patch.object(safe_storage.sys, "platform", "win32"), \
                    patch.object(safe_storage, "_dpapi_decrypt", return_value=b"k" * 32) as dpapi, \
                    patch.object(safe_storage, "_aes_gcm_decrypt", create=True, return_value=b"test-token") as aes:
                self.assertEqual(safe_storage.read_refresh_token(root), "test-token")
                dpapi.assert_called_once_with(b"wrapped-key")
                aes.assert_called_once_with(b"k" * 32, nonce, ciphertext, tag)

    def test_missing_or_invalid_local_state_returns_none(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "refresh-token.enc").write_bytes(b"v10" + b"x" * 40)
            with patch.object(safe_storage.sys, "platform", "win32"):
                self.assertIsNone(safe_storage.read_refresh_token(root))
                (root / "Local State").write_text("{}")
                self.assertIsNone(safe_storage.read_refresh_token(root))

    def test_raw_dpapi(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "refresh-token.enc").write_bytes(safe_storage._DPAPI_MAGIC + b"test")
            with patch.object(safe_storage.sys, "platform", "win32"), \
                    patch.object(safe_storage, "_dpapi_decrypt", return_value=b"test-token"):
                self.assertEqual(safe_storage.read_refresh_token(root), "test-token")

    @unittest.skipUnless(sys.platform == "win32", "需要 Windows CNG")
    def test_cng_known_vector_and_invalid_tag(self):
        # NIST AES-256-GCM: zero key, zero nonce, one zero plaintext block.
        key, nonce = bytes(32), bytes(12)
        ciphertext = bytes.fromhex("cea7403d4d606b6e074ec5d3baf39d18")
        tag = bytes.fromhex("d0d1c8a799996bf0265b98b5d48ab919")
        self.assertEqual(safe_storage._aes_gcm_decrypt(key, nonce, ciphertext, tag), bytes(16))
        with self.assertRaises(OSError):
            safe_storage._aes_gcm_decrypt(key, nonce, ciphertext, bytes(16))


if __name__ == "__main__":
    unittest.main()
