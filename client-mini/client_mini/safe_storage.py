"""读取 Electron safeStorage 凭证,不写出或记录明文。

Windows 支持裸 DPAPI 和 v10(AES-256-GCM + Local State 中 DPAPI 保护的密钥)。
使用系统 API,零第三方依赖;须与正式客户端在同机同一 Windows 用户下运行。
macOS/Linux 的 Keychain/libsecret 格式不支持。
"""

import base64
import json
import sys
from pathlib import Path

from . import log

logger = log.get()
_DPAPI_MAGIC = b"\x01\x00\x00\x00\xd0\x8c"


def read_refresh_token(config_dir: Path) -> str | None:
    """解密失败时返回 None,绝不将未认证数据作为 token 使用。"""
    try:
        enc_path = Path(config_dir) / "refresh-token.enc"
        if not enc_path.exists():
            logger.info(f"[safe-storage] 未找到 {enc_path}(未登录过?)")
            return None
        blob = enc_path.read_bytes()
        if not blob:
            return None
        if sys.platform != "win32":
            logger.error("[safe-storage] 自动解密仅支持 Windows,请配置 auth.refresh_token")
            return None
        if blob.startswith(_DPAPI_MAGIC):
            plain = _dpapi_decrypt(blob)
        elif blob.startswith(b"v10"):
            plain = _decrypt_v10(Path(config_dir), blob)
        else:
            logger.error("[safe-storage] 未支持的加密格式,请配置 auth.refresh_token")
            return None
        if plain is None:
            logger.error("[safe-storage] DPAPI 解密失败,请确认同机同一 Windows 用户")
            return None
        token = plain.decode("utf-8").strip()
        if token:
            logger.info("[safe-storage] 已解密 refresh_token")
        return token or None
    except (OSError, ValueError, KeyError, TypeError):
        # 不记录异常内容,避免 JSON/Unicode 错误泄漏凭证片段。
        logger.error("[safe-storage] 解密失败:请检查 Local State、文件格式及 Windows 用户")
        return None


def _decrypt_v10(config_dir: Path, blob: bytes) -> bytes | None:
    if len(blob) < 3 + 12 + 16:
        raise ValueError("v10 数据长度不足")
    state = json.loads((config_dir / "Local State").read_text(encoding="utf-8"))
    wrapped = base64.b64decode(state["os_crypt"]["encrypted_key"], validate=True)
    if not wrapped.startswith(b"DPAPI") or len(wrapped) <= 5:
        raise ValueError("Local State 密钥格式不支持")
    key = _dpapi_decrypt(wrapped[5:])
    if key is None:
        return None
    if len(key) != 32:
        raise ValueError("AES-256 密钥长度错误")
    return _aes_gcm_decrypt(key, blob[3:15], blob[15:-16], blob[-16:])


def _dpapi_decrypt(blob: bytes) -> bytes | None:
    import ctypes as c
    from ctypes import wintypes as w

    class DATA_BLOB(c.Structure):
        _fields_ = [("cbData", w.DWORD), ("pbData", c.c_void_p)]

    crypt32 = c.WinDLL("crypt32", use_last_error=True)
    kernel32 = c.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        c.POINTER(DATA_BLOB), c.c_void_p, c.c_void_p, c.c_void_p,
        c.c_void_p, w.DWORD, c.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = w.BOOL
    kernel32.LocalFree.argtypes = [c.c_void_p]
    kernel32.LocalFree.restype = c.c_void_p
    src = c.create_string_buffer(blob)
    blob_in = DATA_BLOB(len(blob), c.cast(src, c.c_void_p))
    blob_out = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        c.byref(blob_in), None, None, None, None, 1, c.byref(blob_out)
    ):
        return None
    try:
        return c.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    """Windows CNG AES-GCM,BCryptDecrypt 同时验证认证标签。"""
    import ctypes as c
    from ctypes import wintypes as w

    class AuthInfo(c.Structure):
        _fields_ = [
            ("cbSize", w.ULONG), ("dwInfoVersion", w.ULONG),
            ("pbNonce", c.c_void_p), ("cbNonce", w.ULONG),
            ("pbAuthData", c.c_void_p), ("cbAuthData", w.ULONG),
            ("pbTag", c.c_void_p), ("cbTag", w.ULONG),
            ("pbMacContext", c.c_void_p), ("cbMacContext", w.ULONG),
            ("cbAAD", w.ULONG), ("cbData", c.c_ulonglong), ("dwFlags", w.ULONG),
        ]

    if len(key) != 32 or len(nonce) != 12 or len(tag) != 16:
        raise ValueError("AES-GCM 参数长度错误")
    bcrypt = c.WinDLL("bcrypt")
    ptr, ulong = c.c_void_p, w.ULONG
    signatures = {
        "BCryptOpenAlgorithmProvider": [c.POINTER(ptr), w.LPCWSTR, w.LPCWSTR, ulong],
        "BCryptSetProperty": [ptr, w.LPCWSTR, ptr, ulong, ulong],
        "BCryptGenerateSymmetricKey": [ptr, c.POINTER(ptr), ptr, ulong, ptr, ulong, ulong],
        "BCryptDecrypt": [ptr, ptr, ulong, ptr, ptr, ulong, ptr, ulong, c.POINTER(ulong), ulong],
        "BCryptDestroyKey": [ptr],
        "BCryptCloseAlgorithmProvider": [ptr, ulong],
    }
    for name, args in signatures.items():
        function = getattr(bcrypt, name)
        function.argtypes = args
        function.restype = w.LONG

    def check(status):
        if status < 0:
            raise OSError(f"Windows CNG 失败: 0x{status & 0xffffffff:08x}")

    algorithm, handle = ptr(), ptr()
    try:
        check(bcrypt.BCryptOpenAlgorithmProvider(c.byref(algorithm), "AES", None, 0))
        mode = c.create_unicode_buffer("ChainingModeGCM")
        check(bcrypt.BCryptSetProperty(algorithm, "ChainingMode", mode, c.sizeof(mode), 0))
        key_buffer = c.create_string_buffer(key)
        # Windows 7+ 可由 CNG 分配 key object,DestroyKey 时释放。
        check(bcrypt.BCryptGenerateSymmetricKey(
            algorithm, c.byref(handle), None, 0, key_buffer, len(key), 0
        ))
        nonce_buffer, tag_buffer = c.create_string_buffer(nonce), c.create_string_buffer(tag)
        info = AuthInfo()
        info.cbSize, info.dwInfoVersion = c.sizeof(AuthInfo), 1
        info.pbNonce, info.cbNonce = c.cast(nonce_buffer, ptr), len(nonce)
        info.pbTag, info.cbTag = c.cast(tag_buffer, ptr), len(tag)
        encrypted = c.create_string_buffer(ciphertext)
        output = c.create_string_buffer(max(1, len(ciphertext)))
        written = ulong()
        check(bcrypt.BCryptDecrypt(
            handle, encrypted, len(ciphertext), c.byref(info), None, 0,
            output, len(ciphertext), c.byref(written), 0,
        ))
        return output.raw[:written.value]
    finally:
        if handle:
            bcrypt.BCryptDestroyKey(handle)
        if algorithm:
            bcrypt.BCryptCloseAlgorithmProvider(algorithm, 0)
