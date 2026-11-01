"""交易日历与交易时段判断。

对应 src/main/utils/tools.ts 的 isTradingTime/_isWeekend、
scheduler.ts 的 isMinDataIntradayWindow、
shared/lib/trading-day.ts 的 CSV 日期规范。
"""

import csv
import io
import re
from datetime import datetime
from pathlib import Path

from . import log

logger = log.get()

# YYYY/M/D 或 YYYY-M-D -> YYYY-MM-DD
_DATE_RE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})")


def _pad2(n: int) -> str:
    return f"{n:02d}"


def normalize_trading_date(raw: str) -> str | None:
    s = raw.strip().lstrip("﻿")
    if not s:
        return None
    m = _DATE_RE.match(s)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y}-{_pad2(mo)}-{_pad2(d)}"


def load_trading_days(data_dir: Path) -> list[str]:
    """读 period_offset.csv 第一列「交易日期」,去重按日历序返回。"""
    csv_path = Path(data_dir) / "period_offset.csv"
    if not csv_path.exists():
        logger.warning(f"[period-offset] period_offset.csv 不存在: {csv_path}")
        return []
    try:
        raw = csv_path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Windows 数据文件可能为 GBK;GB18030 兼容 GBK。
            text = raw.decode("gbk")
        with io.StringIO(text, newline="") as f:
            ymds = []
            for row in csv.reader(f):
                if not row:
                    continue
                ymd = normalize_trading_date(row[0])
                if ymd:
                    ymds.append(ymd)
        return sorted(set(ymds))
    except (OSError, UnicodeError, csv.Error) as e:
        logger.error(f"[period-offset] 读取 period_offset.csv 失败: {e}")
        return []


def is_weekend(now: datetime) -> bool:
    return now.weekday() >= 5  # 0=周一 ... 5=周六 6=周日


def is_trading_time(now: datetime | None = None) -> bool:
    """非周末 && 9:00 < now < 15:30(dayjs isBetween 默认双开区间)。"""
    now = now or datetime.now()
    if is_weekend(now):
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 < minutes < 15 * 60 + 30


def is_min_data_intraday_window(now: datetime | None = None) -> bool:
    """min_data 窗口:9:16~11:26 或 13:01~15:01(闭区间)。"""
    now = now or datetime.now()
    minutes = now.hour * 60 + now.minute
    morning = 9 * 60 + 16 <= minutes <= 11 * 60 + 26
    afternoon = 13 * 60 + 1 <= minutes <= 15 * 60 + 1
    return morning or afternoon


def get_local_ymd(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{now.year}-{_pad2(now.month)}-{_pad2(now.day)}"


def is_today_trading_day(data_dir: Path) -> bool:
    """period_offset.csv 交易日历中今天是否为交易日。"""
    calendar = load_trading_days(data_dir)
    return get_local_ymd() in calendar
