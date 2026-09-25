"""服务端业务模块。"""

from __future__ import annotations

from datetime import UTC, datetime

from spe.domain.policy_ast import PolicyDocument
from spe.domain.policy_interpreter import EvalContext, evaluate
from spe.domain.reason_codes import ReasonCode


def _doc(rules: dict) -> PolicyDocument:
    return PolicyDocument.model_validate({"name": "p", "rules": rules})


def _ctx(**kw) -> EvalContext:
    base = dict(
        now=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        user_id="u1",
        user_age=20,
        daily_usage_seconds=0,
        session_elapsed_seconds=0,
    )
    base.update(kw)
    return EvalContext(**base)  # type: ignore[arg-type]


def test_allowed_when_all_rules_pass() -> None:
    doc = _doc(
        {
            "age_gate": {"kind": "age_gate", "min_age": 13},
            "daily_limit": {"kind": "daily_limit", "max_seconds": 3600},
        }
    )
    decision = evaluate(doc, _ctx())
    assert decision.allowed
    assert decision.reason is ReasonCode.ALLOWED


def test_under_min_age_denied() -> None:
    doc = _doc({"age_gate": {"kind": "age_gate", "min_age": 18}})
    decision = evaluate(doc, _ctx(user_age=15))
    assert not decision.allowed
    assert decision.reason is ReasonCode.DENIED_UNDER_MIN_AGE


def test_daily_limit_denied() -> None:
    doc = _doc({"daily_limit": {"kind": "daily_limit", "max_seconds": 600}})
    decision = evaluate(doc, _ctx(daily_usage_seconds=600))
    assert not decision.allowed
    assert decision.reason is ReasonCode.DENIED_DAILY_LIMIT_REACHED


def test_session_limit_denied() -> None:
    doc = _doc({"session_limit": {"kind": "session_limit", "max_seconds": 300}})
    decision = evaluate(doc, _ctx(session_elapsed_seconds=300))
    assert not decision.allowed
    assert decision.reason is ReasonCode.DENIED_SESSION_LIMIT_REACHED


def test_bedtime_curfew_denied() -> None:
    doc = _doc(
        {
            "timezone": "UTC",
            "bedtime": {"kind": "bedtime", "windows": [{"start": "22:00:00", "end": "06:00:00"}]},
        }
    )
    # 23:00 UTC is inside the curfew.
    decision = evaluate(doc, _ctx(now=datetime(2026, 7, 24, 23, 0, tzinfo=UTC)))
    assert not decision.allowed
    assert decision.reason is ReasonCode.DENIED_BEDTIME_CURFEW


def test_exception_waives_daily_limit() -> None:
    doc = _doc(
        {
            "daily_limit": {"kind": "daily_limit", "max_seconds": 600},
            "exceptions": [
                {
                    "exception_id": "e1",
                    "subject_user_id": "u1",
                    "waives": "daily_limit",
                    "approved_by": "admin",
                }
            ],
        }
    )
    decision = evaluate(doc, _ctx(daily_usage_seconds=1000))
    assert decision.allowed
    # The waived step is recorded in the trace.
    assert any(s.outcome == "waived" for s in decision.trace.steps)


def test_trace_is_deterministic() -> None:
    doc = _doc(
        {
            "age_gate": {"kind": "age_gate", "min_age": 13},
            "daily_limit": {"kind": "daily_limit", "max_seconds": 3600},
            "session_limit": {"kind": "session_limit", "max_seconds": 1800},
        }
    )
    ctx = _ctx()
    first = evaluate(doc, ctx).trace.as_list()
    second = evaluate(doc, ctx).trace.as_list()
    assert first == second
    # 规则执行顺序固定为年龄、休息时段、每日额度、单次额度和最终结果。
    rules_in_order = [step["rule"] for step in first]
    assert rules_in_order == ["age_gate", "bedtime", "daily_limit", "session_limit", "final"]


def test_timezone_shifts_bedtime_boundary() -> None:
    # New York is UTC-4 in July. 02:00 UTC == 22:00 local -> inside curfew.
    doc = _doc(
        {
            "timezone": "America/New_York",
            "bedtime": {"kind": "bedtime", "windows": [{"start": "22:00:00", "end": "06:00:00"}]},
        }
    )
    denied = evaluate(doc, _ctx(now=datetime(2026, 7, 25, 2, 0, tzinfo=UTC)))
    assert denied.reason is ReasonCode.DENIED_BEDTIME_CURFEW
    # 18:00 UTC == 14:00 local -> outside curfew.
    allowed = evaluate(doc, _ctx(now=datetime(2026, 7, 24, 18, 0, tzinfo=UTC)))
    assert allowed.allowed
