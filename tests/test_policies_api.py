"""服务端业务模块。"""

from __future__ import annotations

import pytest

from tests.conftest import birth_for_age, headers, sample_policy

pytestmark = pytest.mark.asyncio


async def test_check_endpoint_reports_issues(client) -> None:
    bad = sample_policy(timezone="Nowhere/Nope")
    resp = await client.post("/v1/policies/check", json={"document": bad}, headers=headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["issues"]


async def test_publish_and_versions_increment(client) -> None:
    first = await client.post(
        "/v1/policies", json={"document": sample_policy(name="v1")}, headers=headers()
    )
    assert first.status_code == 201
    assert first.json()["version"] == 1

    second = await client.post(
        "/v1/policies", json={"document": sample_policy(name="v2")}, headers=headers()
    )
    assert second.status_code == 201
    assert second.json()["version"] == 2


async def test_publish_invalid_policy_rejected(client) -> None:
    bad = sample_policy(daily_limit_seconds=100, session_limit_seconds=200)
    resp = await client.post("/v1/policies", json={"document": bad}, headers=headers())
    assert resp.status_code == 422
    assert resp.json()["detail"]["reason"] == "REJECTED_POLICY_INVALID"


async def test_preview_uses_active_policy(client) -> None:
    await client.post(
        "/v1/policies", json={"document": sample_policy(min_age=18)}, headers=headers()
    )
    resp = await client.post(
        "/v1/policies/preview",
        json={"user_id": "u1", "birth_date": birth_for_age(15).isoformat()},
        headers=headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is False
    assert body["reason"] == "DENIED_UNDER_MIN_AGE"
    assert body["trace"][0]["rule"] == "age_gate"


async def test_preview_without_policy_404(client) -> None:
    resp = await client.post(
        "/v1/policies/preview",
        json={"user_id": "u1", "birth_date": birth_for_age(15).isoformat()},
        headers=headers(),
    )
    assert resp.status_code == 404
