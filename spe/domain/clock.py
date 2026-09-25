"""服务端业务模块。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """封装领域状态与业务约束。"""

    def now(self) -> datetime:
        """执行确定性的业务处理。"""
        ...


class SystemClock:
    """封装领域状态与业务约束。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """封装领域状态与业务约束。"""

    def __init__(self, start: datetime) -> None:
        self._now = _ensure_utc(start)

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        self._now = _ensure_utc(value)

    def advance(self, seconds: float) -> datetime:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)
        return self._now


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
