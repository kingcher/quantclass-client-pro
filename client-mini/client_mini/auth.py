"""Token 管理,对应 src/main/lib/tokenStore.ts。

- refresh_token 来自 mini config.json 的 auth.refresh_token
  (正式客户端里由 UI 登录写入 refresh-token.enc,mini 无 UI,直接配置)
- access_token 用 POST {BASE_URL}/user/auth/refresh 获取
- 临近过期(20min)异步刷新,缺失/过期同步刷新,并发去重
"""

import base64
import json
import threading
import time
import urllib.request
from typing import Optional

from . import log

logger = log.get()

NEAR_EXPIRE_S = 20 * 60  # 临近过期阈值(秒)
HTTP_TIMEOUT_S = 15


def decode_jwt_exp(token: str) -> Optional[int]:
    """只解 payload 取 exp,不验签(与 jwt-decode 行为一致)。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        return int(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


class TokenStore:
    def __init__(self, base_url: str, refresh_token: str):
        self._base_url = base_url.rstrip("/")
        self._access_token: Optional[str] = None
        self._access_exp: Optional[int] = None
        self._refresh_token: Optional[str] = refresh_token or None
        self._lock = threading.Lock()
        self._refresh_event: Optional[threading.Event] = None

    @property
    def has_refresh_token(self) -> bool:
        return bool(self._refresh_token)

    # ------------------------------------------------------------------
    def get_access_token(self, force: bool = False) -> Optional[str]:
        """对应 tokenStore.getAccessToken 的四个分支。"""
        with self._lock:
            token, exp = self._access_token, self._access_exp

        if force:
            logger.info("[token][debug] branch=force_refresh")
            return self._refresh()

        now = int(time.time())
        if not token or (exp is not None and exp <= now):
            reason = "missing_access" if not token else "expired"
            logger.info(f"[token][debug] branch=await_refresh reason={reason}")
            return self._refresh()

        if exp is not None and exp - now < NEAR_EXPIRE_S:
            logger.info("[token][debug] branch=async_refresh_near_expire")
            threading.Thread(
                target=self._refresh, kwargs={"invalidate_on_failure": False},
                daemon=True,
            ).start()
            return token

        logger.info("[token][debug] branch=return_current")
        return token

    # ------------------------------------------------------------------
    def _refresh(self, invalidate_on_failure: bool = True) -> Optional[str]:
        """刷新 access_token,并发去重:同一时刻只允许一个真实请求。"""
        with self._lock:
            if self._refresh_event is not None:
                inflight = self._refresh_event
                owner = False
            else:
                inflight = self._refresh_event = threading.Event()
                owner = True

        if not owner:
            logger.info("[token][debug] refresh reuse_inflight")
            inflight.wait()
            return self._access_token

        try:
            refresh_token = self._refresh_token
            if not refresh_token:
                logger.error("[token] 无 refresh_token,无法刷新")
                return None

            req = urllib.request.Request(
                f"{self._base_url}/user/auth/refresh",
                method="POST",
                headers={
                    "Authorization": f"Bearer {refresh_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            access = body.get("access_token") if isinstance(body, dict) else None
            if not body.get("success") or not isinstance(access, str) or not access:
                logger.error(f"[token] 刷新 access_token 失败: 响应无效 {body}")
                return None

            with self._lock:
                self._access_token = access
                self._access_exp = decode_jwt_exp(access)
                # 轮换出的新 refresh_token 就地更新
                new_refresh = body.get("refresh_token")
                if isinstance(new_refresh, str) and new_refresh and new_refresh != self._refresh_token:
                    self._refresh_token = new_refresh
                    logger.info("[token] refresh_token 已轮换(仅内存,请及时更新配置)")
            logger.info("[token] 刷新 access_token 成功")
            return access
        except Exception as e:
            logger.error(f"[token] 刷新 access_token 异常: {e}")
            if invalidate_on_failure:
                logger.error("[token] 会话可能已失效,请重新获取 refresh_token")
            return None
        finally:
            inflight.set()
            with self._lock:
                if self._refresh_event is inflight:
                    self._refresh_event = None
