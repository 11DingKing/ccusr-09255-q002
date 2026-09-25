"""服务端业务模块。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_A, TENANT_B, birth_for_age, headers, sample_policy

pytestmark = pytest.mark.asyncio


async def _publish(client, tenant: str = TENANT_A, **kw) -> int:
    resp = await client.post(
        "/v1/policies", json={"document": sample_policy(**kw)}, headers=headers(tenant)
    )
    assert resp.status_code == 201
    return resp.json()["version"]


async def _start(client, tenant: str = TENANT_A, user_id: str = "u1", age: int = 20, **kw):
    return await client.post(
        "/v1/sessions",
        json={"user_id": user_id, "birth_date": birth_for_age(age).isoformat(), **kw},
        headers=headers(tenant),
    )


async def _hb(client, sid: str, seq: int, total: int, tenant: str = TENANT_A):
    return await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": seq, "watched_seconds_total": total},
        headers=headers(tenant),
    )


async def test_idempotent_start_returns_same_session(client) -> None:
    await _publish(client, bedtime=None)
    first = await _start(client, idempotency_key="k1")
    second = await _start(client, idempotency_key="k1")
    assert first.json()["session"]["id"] == second.json()["session"]["id"]
    assert second.json()["reason"] == "SESSION_STARTED_IDEMPOTENT"


async def test_second_active_session_conflict(client) -> None:
    await _publish(client, bedtime=None)
    first = await _start(client)
    assert first.status_code == 200
    second = await _start(client)
    assert second.status_code == 409
    assert second.json()["detail"]["reason"] == "REJECTED_ACTIVE_SESSION_EXISTS"


async def test_concurrent_starts_only_one_wins(file_client) -> None:
    await _publish(file_client, bedtime=None)
    results = await asyncio.gather(
        *[_start(file_client, user_id="race") for _ in range(5)]
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses.count(200) == 1
    assert statuses.count(409) == 4


async def test_stale_and_out_of_order_heartbeats_ignored(client) -> None:
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]

    assert (await _hb(client, sid, 1, 30)).json()["reason"] == "HEARTBEAT_APPLIED"
    assert (await _hb(client, sid, 3, 90)).json()["reason"] == "HEARTBEAT_APPLIED"
    # Out-of-order (seq 2 < last seq 3) -> ignored.
    assert (await _hb(client, sid, 2, 60)).json()["reason"] == "HEARTBEAT_IGNORED_STALE"
    # Duplicate (seq 3) -> ignored.
    assert (await _hb(client, sid, 3, 90)).json()["reason"] == "HEARTBEAT_IGNORED_STALE"

    usage = await client.get(f"/v1/sessions/{sid}/usage", headers=headers())
    # seq1 (30) + seq3 delta (90-30=60) == 90; stale beats credited nothing.
    assert usage.json()["session"]["total_watched_seconds"] == 90


async def test_tenant_isolation(client) -> None:
    await _publish(client, tenant=TENANT_A, bedtime=None)
    await _publish(client, tenant=TENANT_B, bedtime=None)
    sid = (await _start(client, tenant=TENANT_A)).json()["session"]["id"]
    # Tenant B cannot see tenant A's session.
    resp = await client.get(f"/v1/sessions/{sid}/usage", headers=headers(TENANT_B))
    assert resp.status_code == 404


async def test_cross_midnight_split(client, clock) -> None:
    # America/New_York: local midnight is 04:00 UTC (July, UTC-4).
    await _publish(
        client,
        timezone="America/New_York",
        daily_limit_seconds=80000,
        session_limit_seconds=80000,
        bedtime=None,
    )
    sid = (await _start(client)).json()["session"]["id"]

    # A single heartbeat straddling local midnight: 60s ending at 04:00:30 UTC
    # (== 00:00:30 local) splits 30s to 07-23 and 30s to 07-24.
    clock.set(datetime(2026, 7, 24, 4, 0, 30, tzinfo=UTC))
    resp = await _hb(client, sid, 1, 60)
    body = resp.json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["credited_seconds"] == 60
    assert body["extra"]["per_day"] == {"2026-07-23": 30, "2026-07-24": 30}


async def test_daily_ledger_survives_session_restart(client) -> None:
    """执行确定性的业务处理。"""
    await _publish(
        client, daily_limit_seconds=100, session_limit_seconds=100, bedtime=None, min_age=None
    )
    # First session consumes 60s of the 100s daily budget.
    s1 = (await _start(client)).json()["session"]["id"]
    await _hb(client, s1, 1, 60)
    await client.post(f"/v1/sessions/{s1}/end", headers=headers())

    # Second session on the same local day starts against the remaining 40s.
    s2 = (await _start(client)).json()["session"]["id"]
    # Try to watch 60 more; only 40 should be creditable, then daily limit ends it.
    resp = await _hb(client, s2, 1, 60)
    body = resp.json()
    assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
    assert body["extra"]["limit_reason"] == "DENIED_DAILY_LIMIT_REACHED"
    assert body["extra"]["credited_seconds"] == 40  # not 60 — truncated to remaining budget


async def test_heartbeat_truncates_to_session_budget(client) -> None:
    """执行确定性的业务处理。"""
    await _publish(client, session_limit_seconds=50, daily_limit_seconds=10000, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]
    resp = await _hb(client, sid, 1, 80)  # proposes 80 but only 50 allowed
    body = resp.json()
    assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
    assert body["extra"]["limit_reason"] == "DENIED_SESSION_LIMIT_REACHED"
    assert body["extra"]["credited_seconds"] == 50
    assert body["session"]["total_watched_seconds"] == 50  # exactly the cap, never above


async def test_version_pinning_does_not_change_history(client) -> None:
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]
    # Publish v2 with a tiny session limit.
    await _publish(
        client, name="v2", session_limit_seconds=5, daily_limit_seconds=10000, bedtime=None
    )
    # The in-flight session still honours v1's generous limit.
    resp = await _hb(client, sid, 1, 90)
    body = resp.json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["session"]["policy_version"] == 1
    assert body["session"]["total_watched_seconds"] == 90


async def test_dynamic_age_from_birth_date(client, clock) -> None:
    """执行确定性的业务处理。"""
    await _publish(client, min_age=18, daily_limit_seconds=10000,
                   session_limit_seconds=10000, bedtime=None)
    # Born exactly 18 years before the reference day -> allowed.
    resp = await _start(client, age=18)
    assert resp.json()["reason"] == "SESSION_STARTED"


async def test_replay_reproduces_settlement(client) -> None:
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = (await _start(client)).json()["session"]["id"]
    for seq, total in [(1, 30), (2, 70), (3, 120)]:
        await _hb(client, sid, seq, total)

    replay = await client.get(f"/v1/sessions/{sid}/replay", headers=headers())
    assert replay.status_code == 200
    steps = replay.json()["steps"]
    assert [s["seq"] for s in steps] == [1, 2, 3]
    assert steps[-1]["total_watched_seconds"] == 120
    assert all(s["allowed"] for s in steps)
    # Replay derives age dynamically (no hard-coded 130).
    assert all(s["age"] == 20 for s in steps)
