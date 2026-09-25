"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from spe.domain.policy_interpreter import DecisionTrace
from spe.domain.reason_codes import ReasonCode
from spe.domain.session import Session, SessionStatus


@dataclass
class PolicyPublished:
    """封装领域状态与业务约束。"""

    policy_id: str
    tenant_id: str
    version: int
    name: str


@dataclass
class PreviewResult:
    """封装领域状态与业务约束。"""

    allowed: bool
    reason: ReasonCode
    trace: list[dict[str, str | None]]


@dataclass
class SessionView:
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
    ended_at: datetime | None
    total_watched_seconds: int

    @classmethod
    def of(cls, s: Session) -> SessionView:
        return cls(
            id=s.id,
            tenant_id=s.tenant_id,
            user_id=s.user_id,
            policy_id=s.policy_id,
            policy_version=s.policy_version,
            status=s.status,
            birth_date=s.birth_date,
            started_at=s.started_at,
            updated_at=s.updated_at,
            ended_at=s.ended_at,
            total_watched_seconds=s.total_watched_seconds,
        )


@dataclass
class ActionResult:
    """封装领域状态与业务约束。"""

    reason: ReasonCode
    ok: bool
    session: SessionView | None = None
    trace: list[dict[str, str | None]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        reason: ReasonCode,
        session: Session | None = None,
        trace: DecisionTrace | None = None,
        **extra: Any,
    ) -> ActionResult:
        return cls(
            reason=reason,
            ok=True,
            session=SessionView.of(session) if session else None,
            trace=trace.as_list() if trace else [],
            extra=extra,
        )

    @classmethod
    def rejected(
        cls,
        reason: ReasonCode,
        session: Session | None = None,
        trace: DecisionTrace | None = None,
        **extra: Any,
    ) -> ActionResult:
        return cls(
            reason=reason,
            ok=False,
            session=SessionView.of(session) if session else None,
            trace=trace.as_list() if trace else [],
            extra=extra,
        )
