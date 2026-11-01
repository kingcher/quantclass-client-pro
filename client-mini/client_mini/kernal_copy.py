"""启动时从 data_dir/code 同步内核到自定义 code_dir,不访问远程接口。"""

import shutil
import tempfile
from pathlib import Path

from . import log
from .config import IS_WINDOWS, MiniConfig
from .kernel_state import is_kernel_busy

KERNELS = ("fuel", "fusion", "aqua", "rocket", "scm")
logger = log.get()


def _markers(directory: Path, kernel: str) -> list[Path]:
    return sorted(path for path in directory.glob(f"{kernel}*.yml") if path.is_file())


def _binary(directory: Path, kernel: str) -> Path:
    return directory / kernel / (f"{kernel}.exe" if IS_WINDOWS else kernel)


def copy_kernels(cfg: MiniConfig) -> list[str]:
    """返回已同步的内核名。复制失败则抛出异常,阻止调度启动。

    默认目录及其子目录不需要同步;源缺失时保留目标,不删除已有内核。
    版本以 .yml 文件名为准,源标记不唯一时拒绝猜测。
    """
    source = cfg.default_code_dir.resolve()
    if not source.is_dir():
        logger.warning(f"[kernal-copy] 默认内核目录不存在,跳过: {source}")
        return []

    copied = []
    for kernel in KERNELS:
        target = cfg.get_kernel_code_dir(kernel).resolve()
        if target.is_relative_to(source):
            continue
        if source.is_relative_to(target):
            raise ValueError(f"{kernel} 代码目录不能是 default_code_dir 的父目录")
        markers = _markers(source, kernel)
        if not markers or not _binary(source, kernel).is_file():
            logger.info(f"[kernal-copy] {kernel} 源版本或二进制缺失,跳过")
            continue
        if len(markers) != 1:
            raise ValueError(f"{source} 中 {kernel} 的版本标记不唯一")
        marker = markers[0]
        old_markers = _markers(target, kernel)
        if ([p.name for p in old_markers] == [marker.name]
                and _binary(target, kernel).is_file()):
            continue
        if is_kernel_busy(cfg, kernel):
            raise RuntimeError(f"{kernel} 正在运行或更新,不能覆盖内核")
        destination = target / kernel
        if destination.is_symlink():
            raise ValueError(f"目标内核目录不能是符号链接: {destination}")
        target.mkdir(parents=True, exist_ok=True)
        # 先完整复制到同盘临时目录,避免复制失败破坏已有内核。
        with tempfile.TemporaryDirectory(prefix=f".{kernel}-copy-", dir=target) as temp:
            staging = Path(temp)
            shutil.copytree(source / kernel, staging / kernel)
            shutil.copy2(marker, staging / marker.name)
            backup = staging / "previous"
            if destination.exists():
                destination.rename(backup)
            try:
                (staging / kernel).rename(destination)
            except OSError:
                if backup.exists():
                    backup.rename(destination)
                raise
            # 内核完整落盘后才发布新版本标记;失败时下次启动会重新同步。
            for old_marker in old_markers:
                old_marker.unlink()
            (staging / marker.name).replace(target / marker.name)
        copied.append(kernel)
        logger.info(f"[kernal-copy] {kernel}: {marker.stem}, {source} → {target}")
    return copied
