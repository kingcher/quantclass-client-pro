"""配置与状态存储。

mini 通过 client_config 只读QuantclassClient配置,可覆盖数据、内核和状态目录。
StateStore 保存 mini 独立的运行时可变状态。
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
    """只读QuantclassClient配置,FUEL_CLIENT_CONFIG_PATH 始终指向它。"""

    def __init__(self, config_path: str | Path):
        bootstrap_path = Path(config_path).expanduser().resolve()
        if bootstrap_path.exists():
            self.bootstrap: dict = json.loads(bootstrap_path.read_text(encoding="utf-8"))
        else:
            self.bootstrap = {}

        client_config = self.bootstrap.get("client_config")
        if not client_config:
            appdata = os.environ.get("APPDATA")
            if not appdata:
                raise ValueError("未配置 client_config,且未设置 APPDATA 环境变量")
            client_config = str(Path(appdata) / "QuantclassClient" / "config.json")
        self.path = self._abs(client_config, bootstrap_path.parent)
        if not self.path.exists():
            raise FileNotFoundError(f"QuantclassClient config.json 不存在: {self.path}")
        self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        self.config_dir = self.path.parent

        data_dir = self.bootstrap.get("data_dir")
        if data_dir:
            self.data_dir = self._abs(data_dir, bootstrap_path.parent)
        else:
            all_data_path = self.get_setting("all_data_path", "")
            if not all_data_path:
                raise ValueError(
                    "请配置 client_mini 的 data_dir 或QuantclassClient的数据中心地址"
                )
            self.data_dir = self._abs(all_data_path, self.config_dir)

        self.default_code_dir: Path = self.data_dir / "code"
        code_dir = self.bootstrap.get("code_dir")
        self.code_dir = (
            self._abs(code_dir, bootstrap_path.parent)
            if code_dir
            else self.default_code_dir
        )

        self.kernel_code_dirs: dict[str, Path] = {
            kernel: self._abs(value, bootstrap_path.parent) if value else self.code_dir
            for kernel in ("fuel", "fusion", "aqua", "rocket", "scm")
            for value in [self.bootstrap.get(f"{kernel}_code_dir")]
        }

        fuel_temp_file_path = self.bootstrap.get("fuel_temp_file_path")
        self.fuel_temp_file_path: str = (
            str(self._abs(fuel_temp_file_path, bootstrap_path.parent))
            if fuel_temp_file_path else ""
        )

        reb_path = self.config_dir / "rebalance_time.json"
        if reb_path.exists() and not self.data.get("rebalance_time"):
            try:
                self.data["rebalance_time"] = json.loads(
                    reb_path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"[config] 读取 rebalance_time.json 失败: {e}")

        self.refresh_token: str = (self.bootstrap.get("auth") or {}).get("refresh_token", "")
        if not self.refresh_token:
            from .safe_storage import read_refresh_token

            self.refresh_token = read_refresh_token(self.config_dir) or ""
            if self.refresh_token:
                logger.info("[config] 使用 refresh-token.enc 中的凭证")
        self.base_url: str = self.bootstrap.get("base_url", DEFAULT_BASE_URL)
        # mini 使用独立的运行时状态目录,不写入QuantclassClient配置。
        self.state_dir: Path = self._abs(
            self.bootstrap.get("state_dir") or "./state", bootstrap_path.parent
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[config] 配置源: {self.path}")

    def get_switch(self, key: str, default: bool = False) -> bool:
        """调度开关优先读取 mini 配置,其次读取QuantclassClient配置。"""
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

    def get_kernel_code_dir(self, kernel: str) -> Path:
        """内核独立代码根目录,未配置时回退到 code_dir。"""
        return self.kernel_code_dirs[kernel]

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
    # 注意:mini 对QuantclassClient的 config.json 及其同目录文件完全只读,
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
