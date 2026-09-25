"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from spe.domain.policy_ast import PolicyDocument, RestrictionKind
from spe.domain.reason_codes import ReasonCode
from spe.domain.timeutil import local_time


@dataclass(frozen=True)
class EvalContext:
    """封装领域状态与业务约束。"""

    now: datetime
    user_id: str
    user_age: int
    daily_usage_seconds: int
    session_elapsed_seconds: int


@dataclass(frozen=True)
class TraceStep:
    """封装领域状态与业务约束。"""

    rule: str
    outcome: str  # "pass" | "deny" | "skip" | "waived"
    reason: ReasonCode | None
    detail: str = ""

    def as_dict(self) -> dict[str, str | None]:
        return {
            "rule": self.rule,
            "outcome": self.outcome,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
        }


@dataclass
class DecisionTrace:
    """封装领域状态与业务约束。"""

    steps: list[TraceStep] = field(default_factory=list)

    def record(
        self,
        rule: str,
        outcome: str,
        reason: ReasonCode | None = None,
        detail: str = "",
    ) -> None:
        self.steps.append(TraceStep(rule=rule, outcome=outcome, reason=reason, detail=detail))

    def as_list(self) -> list[dict[str, str | None]]:
        return [step.as_dict() for step in self.steps]


@dataclass(frozen=True)
class Decision:
    """封装领域状态与业务约束。"""

    allowed: bool
    reason: ReasonCode
    trace: DecisionTrace


def _has_exception(doc: PolicyDocument, user_id: str, kind: RestrictionKind) -> bool:
    return any(
        exc.subject_user_id == user_id and exc.waives == kind
        for exc in doc.rules.exceptions
    )


def evaluate(doc: PolicyDocument, ctx: EvalContext) -> Decision:
    """执行确定性的业务处理。"""
    trace = DecisionTrace()
    rules = doc.rules

    # 1. Age gate ------------------------------------------------------------
    if rules.age_gate is not None:
        if ctx.user_age < rules.age_gate.min_age:
            if _has_exception(doc, ctx.user_id, RestrictionKind.MIN_AGE):
                trace.record(
                    "age_gate",
                    "waived",
                    ReasonCode.ALLOWED_BY_EXCEPTION,
                    f"age {ctx.user_age} < {rules.age_gate.min_age}, waived by exception",
                )
            else:
                trace.record(
                    "age_gate",
                    "deny",
                    ReasonCode.DENIED_UNDER_MIN_AGE,
                    f"age {ctx.user_age} < required {rules.age_gate.min_age}",
                )
                return Decision(False, ReasonCode.DENIED_UNDER_MIN_AGE, trace)
        else:
            trace.record("age_gate", "pass", detail=f"age {ctx.user_age} ok")
    else:
        trace.record("age_gate", "skip", detail="no age gate configured")

    # 2. Bedtime curfew ------------------------------------------------------
    if rules.bedtime is not None:
        local = local_time(ctx.now, rules.timezone)
        blocking = next((w for w in rules.bedtime.windows if w.contains(local)), None)
        if blocking is not None:
            if _has_exception(doc, ctx.user_id, RestrictionKind.BEDTIME):
                trace.record(
                    "bedtime",
                    "waived",
                    ReasonCode.ALLOWED_BY_EXCEPTION,
                    f"local {local.isoformat()} in curfew, waived by exception",
                )
            else:
                trace.record(
                    "bedtime",
                    "deny",
                    ReasonCode.DENIED_BEDTIME_CURFEW,
                    f"local {local.isoformat()} within {blocking.start}-{blocking.end}",
                )
                return Decision(False, ReasonCode.DENIED_BEDTIME_CURFEW, trace)
        else:
            trace.record("bedtime", "pass", detail=f"local {local.isoformat()} outside curfew")
    else:
        trace.record("bedtime", "skip", detail="no bedtime curfew configured")

    # 3. Daily limit ---------------------------------------------------------
    if rules.daily_limit is not None:
        if ctx.daily_usage_seconds >= rules.daily_limit.max_seconds:
            if _has_exception(doc, ctx.user_id, RestrictionKind.DAILY_LIMIT):
                trace.record(
                    "daily_limit",
                    "waived",
                    ReasonCode.ALLOWED_BY_EXCEPTION,
                    "daily budget exhausted, waived by exception",
                )
            else:
                trace.record(
                    "daily_limit",
                    "deny",
                    ReasonCode.DENIED_DAILY_LIMIT_REACHED,
                    f"used {ctx.daily_usage_seconds}s >= {rules.daily_limit.max_seconds}s",
                )
                return Decision(False, ReasonCode.DENIED_DAILY_LIMIT_REACHED, trace)
        else:
            trace.record(
                "daily_limit",
                "pass",
                detail=f"used {ctx.daily_usage_seconds}s < {rules.daily_limit.max_seconds}s",
            )
    else:
        trace.record("daily_limit", "skip", detail="no daily limit configured")

    # 4. Session limit -------------------------------------------------------
    if rules.session_limit is not None:
        if ctx.session_elapsed_seconds >= rules.session_limit.max_seconds:
            if _has_exception(doc, ctx.user_id, RestrictionKind.SESSION_LIMIT):
                trace.record(
                    "session_limit",
                    "waived",
                    ReasonCode.ALLOWED_BY_EXCEPTION,
                    "session budget exhausted, waived by exception",
                )
            else:
                trace.record(
                    "session_limit",
                    "deny",
                    ReasonCode.DENIED_SESSION_LIMIT_REACHED,
                    f"elapsed {ctx.session_elapsed_seconds}s >= {rules.session_limit.max_seconds}s",
                )
                return Decision(False, ReasonCode.DENIED_SESSION_LIMIT_REACHED, trace)
        else:
            trace.record(
                "session_limit",
                "pass",
                detail=(
                    f"elapsed {ctx.session_elapsed_seconds}s < {rules.session_limit.max_seconds}s"
                ),
            )
    else:
        trace.record("session_limit", "skip", detail="no session limit configured")

    trace.record("final", "pass", ReasonCode.ALLOWED, "all restrictions satisfied")
    return Decision(True, ReasonCode.ALLOWED, trace)
