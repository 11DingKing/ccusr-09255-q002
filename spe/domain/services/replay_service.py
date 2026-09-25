"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from spe.domain.policy_ast import PolicyDocument
from spe.domain.policy_interpreter import EvalContext, evaluate
from spe.domain.session import Session
from spe.domain.timeutil import age_at, split_watch_window


@dataclass
class HeartbeatRecord:
    """封装领域状态与业务约束。"""

    occurred_at: datetime
    seq: int
    watched_seconds_total: int


@dataclass
class ReplayStep:
    """封装领域状态与业务约束。"""

    seq: int
    credited_seconds: int
    per_day: dict[str, int]
    total_watched_seconds: int
    age: int
    allowed: bool
    reason: str
    trace: list[dict[str, str | None]]


def replay_session(
    session: Session,
    document: PolicyDocument,
    heartbeats: list[HeartbeatRecord],
    max_gap_seconds: int = 90,
    daily_baseline: dict[str, int] | None = None,
) -> list[ReplayStep]:
    """执行确定性的业务处理。"""
    tz = document.rules.timezone
    session_cap = (
        document.rules.session_limit.max_seconds if document.rules.session_limit else None
    )
    daily_cap = document.rules.daily_limit.max_seconds if document.rules.daily_limit else None

    steps: list[ReplayStep] = []
    last_seq = 0
    marker = 0
    total = 0
    daily: dict[str, int] = dict(daily_baseline or {})

    for hb in sorted(heartbeats, key=lambda h: h.seq):
        if hb.seq <= last_seq:
            continue  # stale / duplicate / out-of-order
        proposed = max(0, min(hb.watched_seconds_total - marker, max_gap_seconds))
        marker = max(marker, hb.watched_seconds_total)
        last_seq = hb.seq

        credited = 0
        per_day: dict[str, int] = {}
        hit_limit = False
        reason = "ALLOWED"
        for day, segment in split_watch_window(hb.occurred_at, proposed, tz):
            allow = segment
            if session_cap is not None:
                remaining = session_cap - total
                if allow >= remaining:
                    allow = max(0, remaining)
                    hit_limit = True
                    reason = "DENIED_SESSION_LIMIT_REACHED"
            if daily_cap is not None:
                remaining = daily_cap - daily.get(day, 0)
                if allow >= remaining:
                    allow = max(0, remaining)
                    hit_limit = True
                    reason = "DENIED_DAILY_LIMIT_REACHED"
            if allow > 0:
                daily[day] = daily.get(day, 0) + allow
                total += allow
                credited += allow
                per_day[day] = per_day.get(day, 0) + allow
            if hit_limit:
                break

        age = age_at(session.birth_date, hb.occurred_at, tz)
        # Produce a representative decision trace for this heartbeat instant.
        primary_day = next(iter(per_day), split_watch_window(hb.occurred_at, 1, tz)[0][0])
        ctx = EvalContext(
            now=hb.occurred_at,
            user_id=session.user_id,
            user_age=age,
            daily_usage_seconds=daily.get(primary_day, 0),
            session_elapsed_seconds=total,
        )
        decision = evaluate(document, ctx)
        steps.append(
            ReplayStep(
                seq=hb.seq,
                credited_seconds=credited,
                per_day=per_day,
                total_watched_seconds=total,
                age=age,
                allowed=not hit_limit and decision.allowed,
                reason=reason if hit_limit else decision.reason.value,
                trace=decision.trace.as_list(),
            )
        )
        if hit_limit:
            break

    return steps
