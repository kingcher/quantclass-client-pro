"""配置与状态存储。

支持两种模式:

1. attach 模式(推荐):mini 配置里只写 `client_config` 指向正式客户端的
   config.json(%APPDATA%/QuantclassClient/config.json),其余配置全部
   从它读取——data_dir 取 settings.all_data_path、code_dir 按
   CODE_DATA_PATH 环境变量优先(与 store.getCodeDataPath 规则一致)、
   换仓时间读同目录 rebalance_time.json、real_market_25.json 也只读不写
   (由正式客户端生成,mini 假定配置静态)。mini 只额外需要 refresh_token
   (正式客户端的 refresh-token.enc 是 safeStorage 加密的,读不出来)。

2. standalone 模式:所有配置写在 mini 自己的 config.json
   (见 config.example.json)。

对应原仓库的 electron-store 体系:MiniConfig 兼任 FUEL_CLIENT_CONFIG_PATH,
StateStore 是运行时可变状态(server_port / 心跳时间戳 / 用户缓存)。
"""

import json
import os
import threading
from pathlib import Path

from . import log

logger = log.get()

IS_WINDOWS = os.name == "nt"

DEFAULT_BASE_URL = "https://api.quantclass.cn"


def _atomic_write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(path)


class MiniConfig:
    """加载配置。attach 模式下 self.path 即正式客户端 config.json,
    FUEL_CLIENT_CONFIG_PATH 直接指向它(rocket 读的就是这份)。"""

    def __init__(self, config_path: str | Path):
        bootstrap_path = Path(config_path).expanduser().resolve()
        if not bootstrap_path.exists():
            raise FileNotFoundError(
                f"配置文件不存在: {bootstrap_path}(可从 config.example.json 复制修改)"
            )
        bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))

        client_config = bootstrap.get("client_config")
        if client_config:
            # ==============================================================
            # attach 模式:正式客户端 config.json 为配置源
            # ==============================================================
            self.path = Path(client_config).expanduser()
            if not self.path.is_absolute():
                self.path = (bootstrap_path.parent / self.path).resolve()
            if not self.path.exists():
                raise FileNotFoundError(
                    f"正式客户端 config.json 不存在: {self.path}"
                    f"(Windows 通常在 %APPDATA%\\QuantclassClient\\config.json)"
                )
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
            self.config_dir = self.path.parent

            # -- data_dir / code_dir:与 store.getAllDataPath / getCodeDataPath 同规则
            all_data_path = self.get_setting("all_data_path", "")
            if not all_data_path:
                raise ValueError("正式客户端 config.json 缺少 settings.all_data_path")
            self.data_dir = self._abs(all_data_path, self.config_dir)
            code_env = os.environ.get("CODE_DATA_PATH")
            self.code_dir = (
                self._abs(code_env, bootstrap_path.parent)
                if code_env
                else self.data_dir / "code"
            )

            # -- 换仓时间:正式客户端存在同目录 rebalance_time.json
            reb_path = self.config_dir / "rebalance_time.json"
            if reb_path.exists() and not self.data.get("rebalance_time"):
                try:
                    self.data["rebalance_time"] = json.loads(
                        reb_path.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"[config] 读取 rebalance_time.json 失败: {e}")

            refresh_token = (bootstrap.get("auth") or {}).get("refresh_token", "")
            if not refresh_token:
                # -- 未手动配置时,尝试解密同目录 refresh-token.enc
                #    (仅 Windows 且与正式客户端同一系统用户时可解出)
                from .safe_storage import read_refresh_token

                refresh_token = read_refresh_token(self.config_dir) or ""
                if refresh_token:
                    logger.info("[config] attach 模式将使用 refresh-token.enc 中的凭证")
            base_url = bootstrap.get("base_url", DEFAULT_BASE_URL)
            state_dir_value = bootstrap.get("state_dir", "./state")
            logger.info(f"[config] attach 模式,配置源: {self.path}")
        else:
            # ==============================================================
            # standalone 模式:mini 自己的 config.json
            # ==============================================================
            self.path = bootstrap_path
            self.data = bootstrap
            self.config_dir = self.path.parent
            self.data_dir = self._abs(self.data["data_dir"], self.config_dir)
            self.code_dir = self._abs(self.data["code_dir"], self.config_dir)
            refresh_token = (self.data.get("auth") or {}).get("refresh_token", "")
            base_url = self.data.get("base_url", DEFAULT_BASE_URL)
            state_dir_value = self.data.get("state_dir", "./state")

        self.base_url: str = base_url
        self.refresh_token: str = refresh_token
        self.bootstrap: dict = bootstrap  # mini 自身的引导配置(attach 模式下可放开关覆盖)
        # state_dir 等价于正式客户端的 userData 目录(mini 用独立的,避免互踩)
        self.state_dir: Path = self._abs(state_dir_value, bootstrap_path.parent)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def get_switch(self, key: str, default: bool = False) -> bool:
        """调度开关:bootstrap(attach 模式的 mini 配置)优先,其次正式 config。
        auto_real_trading 正式客户端会持久化;auto_update/auto_min_data 原本
        只在内存,attach 模式下想开就写进 mini 自己的配置里。"""
        if key in self.bootstrap:
            return bool(self.bootstrap[key])
        return bool(self.data.get(key, default))

    @staticmethod
    def _abs(value: str, base: Path) -> Path:
        p = Path(value).expanduser()
        return p if p.is_absolute() else (base / p).resolve()

    # ------------------------------------------------------------------
    # 读取(键名与 electron-store 一致)
    # ------------------------------------------------------------------
    def get_setting(self, key: str, default=""):
        settings = self.data.get("settings", {})
        return settings.get(key) or default

    @property
    def real_market_config(self) -> dict:
        return self.data.get("real_market_config", {})

    @property
    def schedule(self) -> dict:
        return self.data.get("schedule", {})

    def get_nested(self, dotted_key: str, default):
        """按 'pos_mgmt.strategies' 形式取嵌套值,等价 configStore.getValue。"""
        node = self.data
        for seg in dotted_key.split("."):
            if not isinstance(node, dict) or seg not in node:
                return default
            node = node[seg]
        return node

    # ------------------------------------------------------------------
    # 路径(对应 store.getAllDataPath / getCodeDataPath)
    # ------------------------------------------------------------------
    def get_all_data_path(self, nest: str | list[str] | None = None,
                          auto_create: bool = True) -> Path:
        return self._join_under(self.data_dir, nest, auto_create)

    def get_code_data_path(self, nest: str | list[str] | None = None,
                           auto_create: bool = True) -> Path:
        return self._join_under(self.code_dir, nest, auto_create)

    @staticmethod
    def _join_under(root: Path, nest, auto_create: bool) -> Path:
        if nest is None:
            return root
        segments = nest if isinstance(nest, list) else [nest]
        target = root.joinpath(*segments)
        if auto_create:
            target.parent.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------------------
    # 注意:mini 对正式客户端的 config.json 及其同目录文件完全只读,
    # 不提供任何写接口(回调端口取 config 里的 server_port,绑定而非改写)
    # ------------------------------------------------------------------


class StateStore:
    """运行时状态,持久化到 state_dir/state.json,线程安全。"""

    def __init__(self, state_dir: Path):
        self._path = state_dir / "state.json"
        self._lock = threading.Lock()
        self._data: dict = {}
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.error(f"[state] 读取 state.json 失败,从空状态开始: {e}")

    def get(self, key: str, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            _atomic_write_json(self._path, self._data)
