"""日志:对应原仓库 src/main/utils/wiston.ts(仅保留 console + 滚动文件)。"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "client-mini"


def setup(log_dir: str | Path, level: int = logging.INFO) -> logging.Logger:
    """初始化根 logger,console + 5MB 滚动文件,双份都写。"""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:  # 防止重复初始化
        return logger

    logger.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "client-mini.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


def get() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
