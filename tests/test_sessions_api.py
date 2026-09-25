"""服务端业务模块。"""

from __future__ import annotations

import pytest

from tests.conftest import birth_for_age, headers, sample_policy

pytestmark = pytest.mark.asyncio


async def _publish(client, **kw) -> None:
    resp = await client.post(
        "/v1/policies", json={"document": sample_policy(**kw)}, headers=headers()
    )
    assert resp.status_code == 201


async def _start(client, user_id: str = "u1", age: int = 20, **kw):
    return await client.post(
        "/v1/sessions",
        json={"user_id": user_id, "birth_date": birth_for_age(age).isoformat(), **kw},
        headers=headers(),
    )


async def test_start_session_pins_version(client) -> None:
    await _publish(client)
    resp = await _start(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["reason"] == "SESSION_STARTED"
    assert body["session"]["policy_version"] == 1
    assert body["session"]["status"] == "ACTIVE"


async def test_start_without_policy_404(client) -> None:
    resp = await _start(client)
    assert resp.status_code == 404


async def test_start_denied_under_age(client) -> None:
    await _publish(client, min_age=18)
    resp = await _start(client, age=15)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "DENIED_UNDER_MIN_AGE"


async def test_heartbeat_credits_usage(client) -> None:
    await _publish(client, session_limit_seconds=1000, daily_limit_seconds=3600, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]
    resp = await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 1, "watched_seconds_total": 30},
        headers=headers(),
    )
    body = resp.json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["credited_seconds"] == 30
    assert body["session"]["total_watched_seconds"] == 30


async def test_pause_resume_end_flow(client) -> None:
    await _publish(client, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]

    paused = await client.post(f"/v1/sessions/{sid}/pause", headers=headers())
    assert paused.json()["reason"] == "SESSION_PAUSED"

    # Heartbeat on a paused session is rejected.
    hb = await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 1, "watched_seconds_total": 10},
        headers=headers(),
    )
    assert hb.json()["reason"] == "REJECTED_SESSION_NOT_ACTIVE"

    resumed = await client.post(f"/v1/sessions/{sid}/resume", headers=headers())
    assert resumed.json()["reason"] == "SESSION_RESUMED"

    ended = await client.post(f"/v1/sessions/{sid}/end", headers=headers())
    assert ended.json()["reason"] == "SESSION_ENDED"

    # Ending again is idempotent.
    ended2 = await client.post(f"/v1/sessions/{sid}/end", headers=headers())
    assert ended2.json()["reason"] == "SESSION_ENDED"


async def test_heartbeat_ends_session_when_session_limit_hit(client) -> None:
    await _publish(client, session_limit_seconds=60, daily_limit_seconds=3600, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]
    resp = await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 1, "watched_seconds_total": 60},
        headers=headers(),
    )
    body = resp.json()
    assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
    assert body["extra"]["limit_reason"] == "DENIED_SESSION_LIMIT_REACHED"
    assert body["session"]["status"] == "ENDED"


async def test_usage_query(client) -> None:
    await _publish(client, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]
    await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": 1, "watched_seconds_total": 40},
        headers=headers(),
    )
    resp = await client.get(f"/v1/sessions/{sid}/usage", headers=headers())
    assert resp.status_code == 200
    assert resp.json()["session"]["total_watched_seconds"] == 40


async def test_heartbeat_on_missing_session_404(client) -> None:
    await _publish(client)
    resp = await client.post(
        "/v1/sessions/does-not-exist/heartbeat",
        json={"seq": 1, "watched_seconds_total": 10},
        headers=headers(),
    )
    assert resp.status_code == 404
