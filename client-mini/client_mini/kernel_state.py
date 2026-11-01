"""内核运行状态机,对应 src/main/utils/tools.ts 的进程相关函数。

- 各内核的 .py.lock 目录与 PID_LOCK_PATH 映射一致
- 更新锁 update_{kernel}.app.lock 放在 state_dir(对应 userData)
- Windows 下用 taskkill/tasklist,Unix 下用 kill/pgrep
"""

import os
import re
import subprocess
from pathlib import Path

from . import log
from .config import IS_WINDOWS, MiniConfig

logger = log.get()

# 对应 tools.ts 的 PID_LOCK_PATH
PID_LOCK_PATH: dict[str, list[str]] = {
    "fuel": None,  # 特判:code 目录下的 data
    "fusion": ["real_trading", "data", "locker"],
    "aqua": ["real_trading", "data", "locker"],
    "rocket": ["real_trading", "rocket", "data"],
    "scm": None,  # 特判:code 目录下的 scm/data
}

_LOCK_RE = re.compile(r"^(\d+)\.py\.lock$")


def _lock_dir(cfg: MiniConfig, kernel: str) -> Path:
    if kernel == "fuel":
        return cfg.get_kernel_code_dir(kernel) / "data"
    if kernel == "scm":
        return cfg.get_kernel_code_dir(kernel) / "scm" / "data"
    return cfg.get_all_data_path(PID_LOCK_PATH[kernel])


# ----------------------------------------------------------------------
# pid 检查
# ----------------------------------------------------------------------
def is_pid_running(pid: int) -> bool:
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception as e:
        logger.error(f"[pid] 检查进程失败: {e}")
        return False


def _is_kernel_exe_running(kernel: str) -> bool:
    """严格模式的进程名检查(对应 tasklist /FI IMAGENAME)。"""
    try:
        if IS_WINDOWS:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {kernel}.exe", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return f"{kernel}.exe" in out
        out = subprocess.run(
            ["pgrep", "-f", kernel], capture_output=True, text=True, timeout=10
        ).stdout
        return bool(out.strip())
    except Exception as e:
        logger.error(f"[{kernel}] 检查进程失败: {e}")
        return False


# ----------------------------------------------------------------------
# 运行中 / 更新中 / 忙
# ----------------------------------------------------------------------
def is_kernel_running(cfg: MiniConfig, kernel: str, strict: bool = False) -> bool:
    is_running = False

    if strict and IS_WINDOWS:
        is_running = _is_kernel_exe_running(kernel)
        logger.info(f"[{kernel}] 严格模式进程名检查: {is_running}")

    lock_path = _lock_dir(cfg, kernel)
    logger.info(f"[{kernel}] 检查运行中的进程: {lock_path}")
    if not lock_path.exists():
        logger.info(f"[{kernel}] 进程锁文件夹不存在,运行中: {is_running}")
        return is_running

    for lock_file in sorted(lock_path.iterdir()):
        m = _LOCK_RE.match(lock_file.name)
        if not m:
            continue
        pid = int(m.group(1))
        alive = is_pid_running(pid)
        logger.info(f"[{kernel}] {pid} running: {alive}, path: ({lock_file.name})")
        if not alive:
            try:
                lock_file.unlink()
                logger.info(f"[{kernel}] 清理历史 lock 文件: {lock_file.name}")
            except OSError as e:
                logger.error(f"[{kernel}] 清理历史 lock 文件失败: {e}")
        else:
            is_running = True

    logger.info(f"[{kernel}] 运行中: {is_running}")
    return is_running


def is_kernel_updating(cfg: MiniConfig, kernel: str) -> bool:
    return (cfg.state_dir / f"update_{kernel.lower()}.app.lock").exists()


def is_kernel_busy(cfg: MiniConfig, kernel: str) -> bool:
    """对应 isKernalBusy:updating 或 running 都算忙。"""
    strict = kernel in ("rocket", "scm")  # rocket/scm 用严格模式
    running = is_kernel_running(cfg, kernel, strict)
    updating = is_kernel_updating(cfg, kernel)
    if updating:
        logger.info(f"[{kernel}] 内核正在更新")
        return True
    if running:
        logger.info(f"[{kernel}] 内核正在运行")
        return True
    return False


def is_any_kernel_busy(cfg: MiniConfig, kernels: list[str]) -> bool:
    return any(is_kernel_busy(cfg, k) for k in kernels)


# ----------------------------------------------------------------------
# 终止
# ----------------------------------------------------------------------
def _kill_pid(pid: int) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )
        else:
            os.kill(pid, 9)
    except Exception as e:
        logger.error(f"[kill] 终止 {pid} 失败(可能已退出): {e}")


def kill_kernel_by_name(kernel: str) -> None:
    try:
        if IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/IM", f"{kernel}.exe", "/T", "/F"],
                capture_output=True, timeout=15,
            )
        else:
            subprocess.run(["pkill", "-f", kernel], capture_output=True, timeout=15)
        logger.info(f"[{kernel}] 所有 {kernel} 进程已被强制终止")
    except Exception as e:
        logger.error(f"[{kernel}] 强制终止 {kernel} 进程失败: {e}")


def kill_kernel_by_force(cfg: MiniConfig, kernel: str, strict: bool = False) -> None:
    """对应 killKernalByForce:按锁文件逐个杀,strict 再按进程名兜底强杀。"""
    if kernel == "scm":  # scm 常驻服务不写 .py.lock,直接按名杀
        kill_kernel_by_name(kernel)
        return

    lock_path = _lock_dir(cfg, kernel)
    if not lock_path.exists():
        logger.info(f"[{kernel}] 进程锁文件夹不存在,跳过")
        return

    for lock_file in sorted(lock_path.iterdir()):
        m = _LOCK_RE.match(lock_file.name)
        if not m:
            continue
        pid = int(m.group(1))
        logger.info(f"[{kernel}] kill {pid}")
        _kill_pid(pid)
        try:
            lock_file.unlink()
        except OSError:
            pass

    if strict:
        kill_kernel_by_name(kernel)


def kill_all_kernels(cfg: MiniConfig,
                     kernels: list[str] = None) -> None:
    """对应 killAllKernalByForce(退出前兜底清理)。"""
    kernels = kernels or ["fuel", "fusion", "aqua", "rocket", "scm"]
    for kernel in kernels:
        kill_kernel_by_force(cfg, kernel, strict=(kernel == "rocket"))
