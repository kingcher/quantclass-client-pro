"""读取正式客户端的 refresh-token.enc(Electron safeStorage 加密)。

加密格式按平台:
- Windows: DPAPI(CryptProtectData)裸 blob,绑定"加密时的 Windows 用户",
  同一用户下任意进程可用 CryptUnprotectData 解密 —— 本模块支持,零依赖
- macOS / Linux: "v10"/"v11" 前缀 + AES(密钥在 Keychain/libsecret),
  Python 标准库没有 AES,不支持自动解密,需手动提供 refresh_token

因此:mini 与正式客户端跑在同一 Windows 用户下时可自动拿到
refresh_token;跨机器/跨系统拷贝 .enc 文件是解不开的。
"""

import sys
from pathlib import Path

from . import log

logger = log.get()

# DPAPI blob 固定魔数 01 00 00 00 D0 8C ...
_DPAPI_MAGIC = b"\x01\x00\x00\x00\xd0\x8c"


def read_refresh_token(config_dir: Path) -> str | None:
    """尝试从正式客户端 userData 目录解出 refresh_token。"""
    enc_path = Path(config_dir) / "refresh-token.enc"
    if not enc_path.exists():
        logger.info(f"[safe-storage] 未找到 {enc_path}(未登录过?)")
        return None
    blob = enc_path.read_bytes()
    if not blob:
        return None

    if blob.startswith(_DPAPI_MAGIC) and sys.platform == "win32":
        plain = _dpapi_decrypt(blob)
        if plain is None:
            logger.error("[safe-storage] DPAPI 解密失败(换了系统用户?)")
            return None
        token = plain.decode("utf-8", "replace").strip()
        if token:
            logger.info("[safe-storage] 已从 refresh-token.enc 解密 refresh_token")
        return token or None

    if blob[:3] in (b"v10", b"v11"):
        logger.error(
            "[safe-storage] 该 .enc 是 macOS/Linux safeStorage 格式(v10/v11 + AES),"
            "mini 不带 AES 依赖无法解密,请在配置里手动填 auth.refresh_token"
        )
        return None

    logger.error("[safe-storage] 未识别的加密格式,请手动填 auth.refresh_token")
    return None


# ----------------------------------------------------------------------
# Windows DPAPI(仅 win32 下可用,标准库 ctypes 实现)
# ----------------------------------------------------------------------
def _dpapi_decrypt(blob: bytes) -> bytes | None:
    import ctypes
    import ctypes.wintypes as wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    src = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        return None
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
