"""服务端业务模块。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def local_now(instant: datetime, timezone: str) -> datetime:
    """执行确定性的业务处理。"""
    return instant.astimezone(ZoneInfo(timezone))


def local_time(instant: datetime, timezone: str) -> time:
    """执行确定性的业务处理。"""
    return local_now(instant, timezone).timetz().replace(tzinfo=None)


def local_day(instant: datetime, timezone: str) -> date:
    """执行确定性的业务处理。"""
    return local_now(instant, timezone).date()


def local_day_key(instant: datetime, timezone: str) -> str:
    """执行确定性的业务处理。"""
    return local_day(instant, timezone).isoformat()


def age_at(birth_date: date, instant: datetime, timezone: str) -> int:
    """执行确定性的业务处理。"""
    today = local_day(instant, timezone)
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (0 if had_birthday else 1)


def next_local_midnight(instant: datetime, timezone: str) -> datetime:
    """执行确定性的业务处理。"""
    tz = ZoneInfo(timezone)
    aware = instant if instant.tzinfo is not None else instant.replace(tzinfo=UTC)
    local = aware.astimezone(tz)
    next_day = local.date() + timedelta(days=1)
    midnight_local = datetime(next_day.year, next_day.month, next_day.day, tzinfo=tz)
    return midnight_local.astimezone(UTC)


def split_watch_window(
    now: datetime, seconds: int, timezone: str
) -> list[tuple[str, int]]:
    """执行确定性的业务处理。"""
    if seconds <= 0:
        return []
    # Normalise to UTC-aware, whole-second boundaries so durations sum exactly.
    end = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    end = end.astimezone(UTC).replace(microsecond=0)
    cursor = end - timedelta(seconds=seconds)
    remaining = seconds
    segments: list[tuple[str, int]] = []
    while remaining > 0:
        day = local_day(cursor, timezone).isoformat()
        boundary = next_local_midnight(cursor, timezone)
        span = int((boundary - cursor).total_seconds())
        take = remaining if span <= 0 or span >= remaining else span
        segments.append((day, take))
        remaining -= take
        cursor = cursor + timedelta(seconds=take)
    return segments
