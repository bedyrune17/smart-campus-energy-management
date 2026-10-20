"""Time helpers. All timestamps are UTC epoch seconds; buckets follow campus local time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

HOUR = 3600
DAY = 86400


def local_dt(ts: float, offset_s: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone(timedelta(seconds=offset_s)))


def hour_start(ts: float, offset_s: int) -> int:
    """Epoch of the start of the campus-local hour containing ts."""
    return ((int(ts) + offset_s) // HOUR) * HOUR - offset_s


def day_start(ts: float, offset_s: int) -> int:
    """Epoch of campus-local midnight for the day containing ts."""
    return ((int(ts) + offset_s) // DAY) * DAY - offset_s


def day_key(ts: float, offset_s: int) -> str:
    return local_dt(ts, offset_s).strftime("%Y-%m-%d")


def is_weekend(dt: datetime) -> bool:
    return dt.weekday() >= 5
