"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from spe.domain.policy_ast import BedtimeWindow, PolicyDocument, RestrictionKind


@dataclass(frozen=True)
class ValidationIssue:
    """封装领域状态与业务约束。"""

    path: str
    message: str


@dataclass
class ValidationResult:
    """封装领域状态与业务约束。"""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, path: str, message: str) -> None:
        self.issues.append(ValidationIssue(path=path, message=message))


def validate_policy(doc: PolicyDocument) -> ValidationResult:
    """执行确定性的业务处理。"""
    result = ValidationResult()
    rules = doc.rules

    _check_timezone(rules.timezone, result)
    if rules.bedtime is not None:
        _check_bedtime_windows(rules.bedtime.windows, result)
    _check_limit_consistency(doc, result)
    _check_exceptions(doc, result)

    return result


def _check_timezone(tz: str, result: ValidationResult) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        result.add("rules.timezone", f"unknown IANA timezone: {tz!r}")


def _check_bedtime_windows(windows: list[BedtimeWindow], result: ValidationResult) -> None:
    # Detect overlaps by expanding each window to minute-of-day coverage.
    covered: set[int] = set()
    for idx, window in enumerate(windows):
        minutes = _window_minutes(window)
        overlap = covered & minutes
        if overlap:
            result.add(
                f"rules.bedtime.windows[{idx}]",
                "bedtime window overlaps another window",
            )
        covered |= minutes


def _window_minutes(window: BedtimeWindow) -> set[int]:
    start = window.start.hour * 60 + window.start.minute
    end = window.end.hour * 60 + window.end.minute
    if start < end:
        return set(range(start, end))
    # Wrapping window.
    return set(range(start, 24 * 60)) | set(range(0, end))


def _check_limit_consistency(doc: PolicyDocument, result: ValidationResult) -> None:
    rules = doc.rules
    if rules.session_limit is not None and rules.daily_limit is not None:
        if rules.session_limit.max_seconds > rules.daily_limit.max_seconds:
            result.add(
                "rules.session_limit.max_seconds",
                "session limit exceeds daily limit and can never be reached",
            )


def _check_exceptions(doc: PolicyDocument, result: ValidationResult) -> None:
    rules = doc.rules
    present: set[RestrictionKind] = set()
    if rules.age_gate is not None:
        present.add(RestrictionKind.MIN_AGE)
    if rules.daily_limit is not None:
        present.add(RestrictionKind.DAILY_LIMIT)
    if rules.session_limit is not None:
        present.add(RestrictionKind.SESSION_LIMIT)
    if rules.bedtime is not None:
        present.add(RestrictionKind.BEDTIME)

    seen_ids: set[str] = set()
    for idx, exc in enumerate(rules.exceptions):
        if exc.exception_id in seen_ids:
            result.add(
                f"rules.exceptions[{idx}].exception_id",
                f"duplicate exception id: {exc.exception_id!r}",
            )
        seen_ids.add(exc.exception_id)
        if exc.waives not in present:
            result.add(
                f"rules.exceptions[{idx}].waives",
                f"exception waives {exc.waives.value!r} but the policy has no such restriction",
            )
