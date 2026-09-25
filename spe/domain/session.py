"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class SessionStatus(StrEnum):
    """封装领域状态与业务约束。"""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


@dataclass
class Session:
    """封装领域状态与业务约束。"""

    id: str
    tenant_id: str
    user_id: str
    policy_id: str
    policy_version: int
    status: SessionStatus
    birth_date: date
    started_at: datetime
    updated_at: datetime
    ended_at: datetime | None = None

    # Idempotent accounting state.
    last_seq: int = 0
    watched_seconds_marker: int = 0
    total_watched_seconds: int = 0
