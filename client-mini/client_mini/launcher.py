"""内核启动器,对应 src/main/lib/process.ts 的 ProcessManage + execBin。

差异说明(见 README):
- 内核缺失时只告警不自动下载(原 execBin 会触发 updateKernal),
  mini 约定内核二进制手动放置到 code_dir/{kernel}/{kernel}(.exe)
- stdout/stderr 写入日志而非转发渲染进程
"""

import os
import subprocess
import threading
import time
from pathlib import Path

from . import log
from .config import IS_WINDOWS, MiniConfig
from .kernel_state import is_kernel_running, kill_kernel_by_force
from .permission import check_permission, get_select_kernel

logger = log.get()

# 固定环境变量(对应 execBin 中的 process.env 赋值)
FIXED_ENV = {
    "PYTHON8": "1",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf8",
}


class ProcessManager:
    """对应 ProcessManage:登记 spawn 出的内核进程,退出时清理进程树。"""

    def __init__(self, cfg: MiniConfig = None):
        self._cfg = cfg  # kill_process 时用于 kill_kernel_by_force
        self._processes: dict[int, dict] = {}
        self._lock = threading.Lock()

    def spawn_process(self, bin_path, args, env, action, kernel):
        popen = subprocess.Popen(
            [str(bin_path), *args],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        entry = {
            "action": action,
            "created_at": time.strftime("%Y-%m-%d %H:%M"),
            "kernel": kernel,
            "popen": popen,
        }
        with self._lock:
            self._processes[popen.pid] = entry

        # stdout/stderr 泵线程(对应 handlePythonProcess)
        threading.Thread(
            target=self._pump_stdout, args=(popen, kernel, action), daemon=True
        ).start()
        threading.Thread(
            target=self._pump_stderr, args=(popen, kernel, action), daemon=True
        ).start()
        # 退出清理线程(对应 childProcess.on("exit"))
        threading.Thread(
            target=self._on_exit, args=(popen, kernel, action), daemon=True
        ).start()
        logger.info(f"[{kernel}] 已启动 pid={popen.pid} action={action}")
        return popen

    def _pump_stdout(self, popen, kernel, action):
        try:
            for raw in iter(popen.stdout.readline, b""):
                logger.info(f"[{kernel}|out] {raw.decode('utf-8', 'replace').rstrip()}")
        except Exception as e:
            logger.error(f"[{kernel}] stdout 读取异常: {e}")

    def _pump_stderr(self, popen, kernel, action):
        import re

        try:
            for raw in iter(popen.stderr.readline, b""):
                text = raw.decode("utf-8", "replace")
                # 过滤进度条(对应 isProgressBar 判断)
                if "\r" in text or re.search(r"\d+%\|", text):
                    continue
                logger.error(f"[{kernel}] {action} 标准错误: {text.rstrip()}")
        except Exception as e:
            logger.error(f"[{kernel}] stderr 读取异常: {e}")

    def _on_exit(self, popen, kernel, action):
        code = popen.wait()
        logger.info(f"[{kernel}] {action} 以代码 {code} 退出")
        with self._lock:
            self._processes.pop(popen.pid, None)

    # ------------------------------------------------------------------
    def has_process_with_action(self, action: str) -> bool:
        with self._lock:
            return any(p["action"] == action for p in self._processes.values())

    def get_running_processes(self) -> list[dict]:
        with self._lock:
            return [
                {"pid": pid, **{k: v for k, v in entry.items() if k != "popen"}}
                for pid, entry in self._processes.items()
            ]

    def kill_process(self, pid: int) -> bool:
        with self._lock:
            entry = self._processes.get(pid)
        if not entry:
            logger.info(f"未找到 PID 为 {pid} 的进程")
            return False
        try:
            entry["popen"].kill()
        except Exception as e:
            logger.error(f"终止进程 {pid} 时出错: {e}")
        if entry["action"] == "启动 rocket" and self._cfg is not None:
            kill_kernel_by_force(self._cfg, "rocket", strict=True)
        return True


process_manager = ProcessManager()


def get_kernel_path(cfg: MiniConfig, kernel: str) -> Path:
    """对应 utils/common.ts 的 getKernalPath:{code_dir}/{kernel}/{kernel}(.exe)。"""
    bin_path = cfg.get_kernel_code_dir(kernel) / kernel / kernel
    if IS_WINDOWS:
        bin_path = bin_path.with_suffix(".exe")
    if bin_path.exists():
        try:
            os.chmod(bin_path, 0o755)
        except OSError as e:
            logger.error(f"[getKernelPath]: {bin_path} 内核权限设置失败: {e}")
    else:
        try:
            os.system(f"chmod +x {bin_path}")
        except Exception:
            pass
    return bin_path


def exec_bin(cfg: MiniConfig, tokens, user_account, args: list[str],
             action: str, kernel: str = "fuel",
             extra_env: dict | str | None = None):
    """对应 execBin 的完整条件链与进程启动。

    cfg: MiniConfig;tokens: TokenStore;user_account: user store 的返回值
    """
    try:
        api_key = cfg.get_setting("api_key", "")
        hid = cfg.get_setting("hid", "")

        if kernel != "fuel":
            is_anonymous = (not api_key and not hid) or not user_account.get("is_logged_in")
            is_allowed = check_permission(
                user_account.get("permissions"), "isMember", "isStock"
            )
            if is_anonymous:
                logger.warning(f"[exec-{kernel}] 未登录,不调用内核")
                return None
            if not is_allowed:
                logger.warning(f"[exec-{kernel}] 无分享会或股票权限,不调用内核")
                return None
            if kernel in ("fusion", "aqua"):
                select_kernel = get_select_kernel(user_account.get("permissions"))
                if kernel != select_kernel:
                    logger.warning(
                        f"[exec-{kernel}] 当前身份应使用 {select_kernel} 内核,不调用"
                    )
                    return None

        # -- 内核路径(缺失不自动下载,提示手动安装)
        bin_path = get_kernel_path(cfg, kernel)
        if not bin_path.exists():
            logger.warning(
                f"[exec-{kernel}] 内核不存在: {bin_path}(mini 不自动下载,"
                f"请手动放置 {kernel} 内核到 code_dir)"
            )
            return None

        fuel_running = is_kernel_running(cfg, "fuel")
        fusion_running = is_kernel_running(cfg, "fusion")
        aqua_running = is_kernel_running(cfg, "aqua")
        rocket_running = is_kernel_running(cfg, "rocket", strict=True)
        logger.info(
            f"[exec-{kernel}] fuel({fuel_running})、rocket({rocket_running})、"
            f"fusion({fusion_running})、aqua({aqua_running})"
        )

        # -- 重复启动检查(与 execBin 的顺序一致)
        if (
            fuel_running
            and kernel == "fuel"
            and action == "自动更新所有数据"
            and process_manager.has_process_with_action("自动更新所有数据")
        ):
            logger.warning(f"[exec-{kernel}] 自动更新所有数据仍在运行中,退出 execBin")
            return None
        if (fusion_running or aqua_running) and kernel in ("fusion", "aqua"):
            logger.warning(f"[exec-{kernel}] 仍在运行中,退出 execBin")
            return None
        if rocket_running and kernel == "rocket" and args[0] != "load":
            logger.warning(f"[exec-{kernel}] 仍在运行中,退出 execBin")
            return None
        if (
            kernel in ("fusion", "aqua")
            and action == "选股"
            and process_manager.has_process_with_action("选股")
        ):
            logger.warning(f"[exec-{kernel}] 已存在选股的进程,跳过执行")
            return None

        logger.info(f"[exec-{kernel}] {action} 开始执行...")

        fuel_code_path = str(cfg.get_kernel_code_dir(kernel))
        fuel_pro_trading_path = str(cfg.get_all_data_path("real_trading"))
        access_token = tokens.get_access_token()
        client_config_path = str(cfg.path)  # FUEL_CLIENT_CONFIG_PATH = 正式客户端 config.json

        use_open_sell = str(cfg.real_market_config.get("use_open_sell", "0"))

        env = dict(os.environ)
        env.update(FIXED_ENV)
        env.update(
            {
                "FUEL_CODE_PATH": fuel_code_path,
                "FUEL_CLIENT_CONFIG_PATH": client_config_path,
                "FUEL_PRO_TRADING_PATH": fuel_pro_trading_path,
                "PYTHONPATH": fuel_code_path,
                "FUEL_ACCESS_TOKEN": access_token or "",
                "FUEL_TEMP_FILE_PATH": (
                    extra_env if isinstance(extra_env, str)
                    else cfg.fuel_temp_file_path if kernel == "fuel" else ""
                ),
                "USE_OPEN_SELL": use_open_sell,
            }
        )
        if isinstance(extra_env, dict):
            env.update(extra_env)

        logger.info(f"[exec-{kernel}] 内核路径: {bin_path}")
        logger.info(f"[exec-{kernel}] FUEL_CODE_PATH={fuel_code_path}")
        if kernel == "fuel":
            logger.info(f"[exec-fuel] FUEL_TEMP_FILE_PATH={env['FUEL_TEMP_FILE_PATH']}")
        logger.info(f"[exec-{kernel}] FUEL_CLIENT_CONFIG_PATH={client_config_path}")
        logger.info(f"[exec-{kernel}] FUEL_PRO_TRADING_PATH={fuel_pro_trading_path}")
        logger.info(f"~% {kernel} {' '.join(args)}")

        return process_manager.spawn_process(bin_path, args, env, action, kernel)
    except Exception as e:
        logger.error(f"[exec-{kernel}] 内核 {action} 执行时发生错误: {e}")
        return None
