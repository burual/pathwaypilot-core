"""utils/time.py — 时间工具（时间戳转换 / 格式化 / 时区处理）。

单一职责：时间处理。**不 import 本包内其他 utils 模块**。

⚠️ 本模块名为 time，与标准库 time 同名：模块内 `import time as _time` 取的是标准库
（Python 绝对导入），不受影响；调用方请用 `from utils import time as time_utils` 避免歧义。
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone, tzinfo
from typing import Callable, Optional, Union
from zoneinfo import ZoneInfo

__all__ = [
    "ISO_FORMAT",
    "UTC",
    "elapsed_ms",
    "format_datetime",
    "from_millis",
    "from_timestamp",
    "humanize_duration",
    "now_local",
    "now_utc",
    "parse_datetime",
    "start_timer",
    "to_iso",
    "to_millis",
    "to_timestamp",
    "to_timezone",
]

UTC = timezone.utc
ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"
_ISO_Z = "%Y-%m-%dT%H:%M:%S.%fZ"
_DEFAULT_PARSE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
    "%d-%m-%Y",
)

TzLike = Union[str, tzinfo, None]


def _as_tz(tz: TzLike) -> tzinfo:
    """把时区入参统一为 tzinfo：str 走 ZoneInfo，None 视为 UTC。"""
    if tz is None:
        return UTC
    if isinstance(tz, str):
        return ZoneInfo(tz)
    return tz


def now_utc() -> datetime:
    """当前 UTC 时间（带 tzinfo=UTC）。"""
    return datetime.now(UTC)


def now_local(tz: TzLike = None) -> datetime:
    """当前时间并转换到指定时区（None 表示系统本地时区）。"""
    if tz is None:
        return datetime.now().astimezone()
    return datetime.now(UTC).astimezone(_as_tz(tz))


def to_timestamp(dt: datetime) -> float:
    """datetime -> Unix 秒（naive 视为 UTC）。"""
    moment = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    return moment.timestamp()


def from_timestamp(ts: Union[int, float], tz: TzLike = UTC) -> datetime:
    """Unix 秒 -> datetime（默认 UTC）。"""
    return datetime.fromtimestamp(float(ts), tz=_as_tz(tz))


def to_millis(dt: datetime) -> int:
    """datetime -> Unix 毫秒。"""
    return int(to_timestamp(dt) * 1000)


def from_millis(ms: Union[int, float], tz: TzLike = UTC) -> datetime:
    """Unix 毫秒 -> datetime（默认 UTC）。"""
    return from_timestamp(float(ms) / 1000.0, tz)


def to_iso(dt: Optional[datetime] = None, *, millis: bool = False) -> str:
    """datetime -> ISO 8601 字符串（默认当前 UTC；millis=True 含毫秒 + Z）。"""
    moment = dt or now_utc()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    return moment.strftime(_ISO_Z) if millis else moment.strftime(ISO_FORMAT) + "Z"


def format_datetime(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """按 strftime 格式输出（保持原时区）。"""
    return dt.strftime(fmt)


def parse_datetime(text: str, fmt: Optional[str] = None, tz: TzLike = UTC) -> datetime:
    """解析时间字符串：优先 ISO 8601（含 Z），否则按 fmt 或常见格式依次尝试。"""
    if not text or not text.strip():
        raise ValueError("empty datetime string")
    raw = text.strip()
    if fmt:
        parsed = datetime.strptime(raw, fmt)
        return parsed.replace(tzinfo=_as_tz(tz)) if parsed.tzinfo is None else parsed

    iso = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        pass
    for candidate in _DEFAULT_PARSE_FORMATS:
        try:
            parsed = datetime.strptime(raw, candidate)
            return parsed.replace(tzinfo=_as_tz(tz))
        except ValueError:
            continue
    raise ValueError(f"unrecognized datetime: {text!r}")


def to_timezone(dt: datetime, tz: TzLike) -> datetime:
    """转换到目标时区（naive 先按 UTC 处理）。"""
    moment = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt
    return moment.astimezone(_as_tz(tz))


def humanize_duration(seconds: Union[int, float]) -> str:
    """秒 -> 人类可读时长，如 '2h 3m 4s' / '450ms'。"""
    secs = float(seconds)
    if secs < 0:
        secs = 0.0
    if secs < 1:
        return f"{int(round(secs * 1000))}ms"
    secs = int(secs)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if sec or not parts:
        parts.append(f"{sec}s")
    return " ".join(parts)


def elapsed_ms(start: float, end: Optional[float] = None) -> float:
    """自 start 至 end（默认现在）经过的毫秒数，基于 perf_counter。"""
    return ((_time.perf_counter() if end is None else end) - start) * 1000.0


def start_timer() -> Callable[[], float]:
    """返回一个计时器闭包：调用它得到自创建以来的毫秒数。

    用法：
        elapsed = start_timer()
        ...  # do work
        print(elapsed(), "ms")
    """
    start = _time.perf_counter()

    def _stop() -> float:
        return (_time.perf_counter() - start) * 1000.0

    return _stop
