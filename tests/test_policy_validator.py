"""服务端业务模块。"""

from __future__ import annotations

from spe.domain.policy_ast import PolicyDocument
from spe.domain.policy_validator import validate_policy


def _doc(rules: dict, name: str = "p") -> PolicyDocument:
    return PolicyDocument.model_validate({"name": name, "rules": rules})


def test_valid_policy_passes() -> None:
    doc = _doc(
        {
            "timezone": "America/New_York",
            "age_gate": {"kind": "age_gate", "min_age": 13},
            "daily_limit": {"kind": "daily_limit", "max_seconds": 3600},
            "session_limit": {"kind": "session_limit", "max_seconds": 1800},
            "bedtime": {"kind": "bedtime", "windows": [{"start": "22:00:00", "end": "06:00:00"}]},
        }
    )
    result = validate_policy(doc)
    assert result.ok


def test_unknown_timezone_flagged() -> None:
    doc = _doc({"timezone": "Mars/Phobos"})
    result = validate_policy(doc)
    assert not result.ok
    assert any("timezone" in issue.path for issue in result.issues)


def test_session_limit_exceeding_daily_flagged() -> None:
    doc = _doc(
        {
            "daily_limit": {"kind": "daily_limit", "max_seconds": 600},
            "session_limit": {"kind": "session_limit", "max_seconds": 1200},
        }
    )
    result = validate_policy(doc)
    assert not result.ok
    assert any("session_limit" in issue.path for issue in result.issues)


def test_overlapping_bedtime_windows_flagged() -> None:
    doc = _doc(
        {
            "bedtime": {
                "kind": "bedtime",
                "windows": [
                    {"start": "22:00:00", "end": "23:30:00"},
                    {"start": "23:00:00", "end": "23:45:00"},
                ],
            }
        }
    )
    result = validate_policy(doc)
    assert not result.ok
    assert any("bedtime" in issue.path for issue in result.issues)


def test_exception_without_matching_restriction_flagged() -> None:
    doc = _doc(
        {
            "exceptions": [
                {
                    "exception_id": "e1",
                    "subject_user_id": "u1",
                    "waives": "daily_limit",
                    "approved_by": "admin",
                }
            ]
        }
    )
    result = validate_policy(doc)
    assert not result.ok
    assert any("exceptions" in issue.path for issue in result.issues)


def test_duplicate_exception_id_flagged() -> None:
    doc = _doc(
        {
            "daily_limit": {"kind": "daily_limit", "max_seconds": 600},
            "exceptions": [
                {
                    "exception_id": "e1",
                    "subject_user_id": "u1",
                    "waives": "daily_limit",
                    "approved_by": "a",
                },
                {
                    "exception_id": "e1",
                    "subject_user_id": "u2",
                    "waives": "daily_limit",
                    "approved_by": "a",
                },
            ],
        }
    )
    result = validate_policy(doc)
    assert not result.ok
    assert any("duplicate" in issue.message for issue in result.issues)
