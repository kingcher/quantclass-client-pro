"""用户信息,对应 src/main/lib/userStore.ts + src/main/utils/user.ts。

- POST /user/info/v3 拉用户信息,缓存 15h + 0~4h 随机偏移
- calculatePermissions:isMember / approval.stock / membershipInfo -> permissions 列表
"""

import json
import random
import threading
import time
import urllib.request
from typing import Optional

from . import log
from .auth import TokenStore

logger = log.get()

HTTP_TIMEOUT_S = 15


def calculate_permissions(user: dict) -> list[str]:
    """对应 utils/user.ts 的 calculatePermissions(只产出权限列表)。"""
    permissions: list[str] = []
    if user.get("isMember"):
        permissions.append("isMember")
    if (user.get("approval") or {}).get("stock"):
        permissions.append("isStock")
    if (user.get("approval") or {}).get("crypto"):
        permissions.append("isCrypto")
    if (user.get("approval") or {}).get("block"):
        permissions.append("isBlock")
    membership = user.get("membershipInfo")
    if isinstance(membership, list):
        permissions.extend(str(x) for x in membership if isinstance(x, str))
    return permissions


class UserStore:
    def __init__(self, base_url: str, token_store: TokenStore, state):
        self._base_url = base_url.rstrip("/")
        self._tokens = token_store
        self._state = state  # StateStore,缓存写 state.json
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def get_user_account(self, force: bool = False) -> Optional[dict]:
        """带缓存的用户信息,返回 {user, is_logged_in, permissions}。"""
        base_ttl_s = 15 * 3600
        random_offset = random.random() * 4 * 3600

        with self._lock:
            cached = self._state.get("userData")
        if not (cached or {}).get("WebUserInfo"):
            return self._update_user_info()

        last_update = cached.get("lastUpdateTime") or 0
        if last_update and time.time() * 1000 - last_update < base_ttl_s * 1000 + random_offset * 1000 and not force:
            logger.info("[user] 使用缓存的用户信息(未超过缓存过期时间)")
            return self._build(cached["WebUserInfo"])

        logger.info("[user] 缓存已过期或强制更新,发起新的用户信息请求")
        updated = self._update_user_info()
        return updated or self._build(cached["WebUserInfo"])  # 网络错误退回旧数据

    # ------------------------------------------------------------------
    def _build(self, web_user_info: dict) -> dict:
        user = web_user_info.get("user") or {}
        return {
            "user": user,
            "is_logged_in": bool(web_user_info.get("isLoggedIn")),
            "permissions": calculate_permissions(user),
        }

    def _update_user_info(self) -> Optional[dict]:
        try:
            logger.info("[user] 发起用户信息更新请求")
            token = self._tokens.get_access_token()
            if not token:
                logger.error("[user] 无可用 access_token,获取用户信息失败")
                return None

            req = urllib.request.Request(
                f"{self._base_url}/user/info/v3",
                method="POST",
                data=json.dumps({}).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            # 兼容 {success, data:{...}} 或直接返回用户对象两种包裹
            user = body.get("data") if isinstance(body, dict) else None
            if not isinstance(user, dict):
                user = body if isinstance(body, dict) else None
            if not isinstance(user, dict) or not user:
                logger.error("[user] 获取用户信息失败: 响应数据格式错误")
                return None

            self._state.set(
                "userData",
                {
                    "WebUserInfo": {"user": user, "isLoggedIn": True},
                    "lastUpdateTime": int(time.time() * 1000),
                },
            )
            logger.info("[user] 用户信息已更新并保存")
            return self._build({"user": user, "isLoggedIn": True})
        except Exception as e:
            logger.error(f"[user] 获取用户信息异常: {e}")
            return None
