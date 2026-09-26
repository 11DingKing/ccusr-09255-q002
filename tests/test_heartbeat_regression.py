"""心跳累计值回退与并发结算的不变量测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from spe.app import create_app
from spe.config import Settings
from spe.container import Container
from spe.domain.clock import FixedClock
from spe.domain.ids import SequentialIdGenerator
from spe.domain.services.session_service import SessionService
from spe.infra.db.base import Base
from spe.infra.db.ddl import CREATE_ACTIVE_SESSION_INDEX
from spe.infra.db.models import (
    DailyUsageLedgerModel,
    HeartbeatModel,
    OutboxModel,
    SessionModel,
)
from spe.infra.db.repositories.repositories import (
    SqlDailyUsageLedger,
    SqlHeartbeatRepository,
    SqlPolicyRepository,
    SqlSessionRepository,
)
from tests.conftest import TENANT_A, birth_for_age, headers, sample_policy

pytestmark = pytest.mark.asyncio


async def _publish(client, **kw) -> None:
    resp = await client.post(
        "/v1/policies", json={"document": sample_policy(**kw)}, headers=headers()
    )
    assert resp.status_code == 201


async def _start(client, user_id: str = "u1", age: int = 20) -> str:
    resp = await client.post(
        "/v1/sessions",
        json={"user_id": user_id, "birth_date": birth_for_age(age).isoformat()},
        headers=headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]["id"]


async def _hb(client, sid: str, seq: int, total: int):
    return await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": seq, "watched_seconds_total": total},
        headers=headers(),
    )


@dataclass
class _State:
    last_seq: int
    marker: int
    total: int
    status: str
    ledger: dict[str, int]
    heartbeats: list[tuple[int, int, int]]  # (seq, watched_total, credited)
    outbox_events: list[str]


async def _snapshot(container: Container, sid: str) -> _State:
    """Read the four stores directly to assert cross-store consistency."""
    async with container.session_factory() as db:
        session = (
            await db.execute(select(SessionModel).where(SessionModel.id == sid))
        ).scalar_one()
        ledger_rows = (
            (
                await db.execute(
                    select(DailyUsageLedgerModel).where(
                        DailyUsageLedgerModel.tenant_id == session.tenant_id,
                        DailyUsageLedgerModel.user_id == session.user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        hb_rows = (
            (
                await db.execute(
                    select(HeartbeatModel)
                    .where(HeartbeatModel.session_id == sid)
                    .order_by(HeartbeatModel.seq)
                )
            )
            .scalars()
            .all()
        )
        events = (
            (
                await db.execute(
                    select(OutboxModel.event_type)
                    .where(OutboxModel.aggregate_id == sid)
                    .order_by(OutboxModel.id)
                )
            )
            .scalars()
            .all()
        )
    return _State(
        last_seq=session.last_seq,
        marker=session.watched_seconds_marker,
        total=session.total_watched_seconds,
        status=session.status,
        ledger={row.local_day: row.seconds for row in ledger_rows},
        heartbeats=[
            (row.seq, row.watched_seconds_total, row.credited_seconds) for row in hb_rows
        ],
        outbox_events=list(events),
    )


# -- regression guard ---------------------------------------------------------


async def test_regression_rejected_and_state_untouched(client, initialized_container) -> None:
    """大序号 + 较小累计值：拒绝且不推进游标、不写任何存储。"""
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)

    applied = await _hb(client, sid, 1, 60)
    assert applied.json()["reason"] == "HEARTBEAT_APPLIED"
    before = await _snapshot(initialized_container, sid)

    resp = await _hb(client, sid, 2, 25)  # reinstalled client: bigger seq, smaller total
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["reason"] == "HEARTBEAT_IGNORED_REGRESSION"
    assert body["extra"]["received_seq"] == 2
    assert body["extra"]["last_seq"] == 1
    assert body["extra"]["received_total"] == 25
    assert body["extra"]["watched_seconds_marker"] == 60

    after = await _snapshot(initialized_container, sid)
    assert after == before  # cursor, ledger, heartbeat log and outbox all untouched
    assert after.last_seq == 1
    assert after.marker == 60
    assert after.total == 60
    assert after.heartbeats == [(1, 60, 60)]
    assert after.outbox_events == ["session.started", "session.heartbeat"]


async def test_legitimate_heartbeat_after_regression_settles_on_original_baseline(
    client, initialized_container
) -> None:
    """回退被拒绝后，后续合法心跳仍按原基线结算。"""
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)

    await _hb(client, sid, 1, 60)
    assert (await _hb(client, sid, 2, 20)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"
    # Rejecting the regression is idempotent: the same beat stays rejected.
    assert (await _hb(client, sid, 2, 20)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    # The reinstalled client may even reuse the rejected seq: still accepted,
    # because the cursor never moved.
    resp = await _hb(client, sid, 2, 90)
    body = resp.json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["credited_seconds"] == 30  # 90 - 60, not 90 - 20

    state = await _snapshot(initialized_container, sid)
    assert state.total == 90
    assert sum(state.ledger.values()) == 90
    assert state.heartbeats == [(1, 60, 60), (2, 90, 30)]


async def test_duplicate_and_zero_increment_are_distinguishable(
    client, initialized_container
) -> None:
    """重复投递、真正零增量与累计回退产生可区分的三种结果。"""
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)

    assert (await _hb(client, sid, 1, 60)).json()["reason"] == "HEARTBEAT_APPLIED"

    # Duplicate delivery (same seq): ignored, not ok.
    dup = (await _hb(client, sid, 1, 60)).json()
    assert dup["ok"] is False
    assert dup["reason"] == "HEARTBEAT_IGNORED_STALE"

    # Genuine zero increment (new seq, same cumulative value): applied with 0.
    zero = (await _hb(client, sid, 2, 60)).json()
    assert zero["ok"] is True
    assert zero["reason"] == "HEARTBEAT_APPLIED"
    assert zero["extra"]["credited_seconds"] == 0

    # Regression (new seq, smaller cumulative value): rejected, distinct reason.
    reg = (await _hb(client, sid, 3, 59)).json()
    assert reg["ok"] is False
    assert reg["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    state = await _snapshot(initialized_container, sid)
    assert state.last_seq == 2  # zero-increment advanced the cursor; regression did not
    assert state.marker == 60
    assert state.total == 60
    assert sum(state.ledger.values()) == 60
    assert state.heartbeats == [(1, 60, 60), (2, 60, 0)]


# -- cross-midnight -----------------------------------------------------------


async def test_cross_midnight_settlement_after_regression(
    client, initialized_container, clock
) -> None:
    """跨午夜窗口在回退心跳之后仍按原基线拆分到账本。"""
    await _publish(
        client,
        timezone="America/New_York",
        daily_limit_seconds=80000,
        session_limit_seconds=80000,
        bedtime=None,
    )
    sid = await _start(client)

    # Local midnight is 04:00 UTC (July, UTC-4): a 60s window ending at
    # 00:00:30 local splits 30s/30s across the two local days.
    clock.set(datetime(2026, 7, 24, 4, 0, 30, tzinfo=UTC))
    first = (await _hb(client, sid, 1, 60)).json()
    assert first["extra"]["per_day"] == {"2026-07-23": 30, "2026-07-24": 30}

    assert (await _hb(client, sid, 2, 15)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    # The next legitimate beat settles 120-60=60s from the preserved baseline,
    # straddling the same midnight boundary.
    third = (await _hb(client, sid, 3, 120)).json()
    assert third["reason"] == "HEARTBEAT_APPLIED"
    assert third["extra"]["credited_seconds"] == 60
    assert third["extra"]["per_day"] == {"2026-07-23": 30, "2026-07-24": 30}

    state = await _snapshot(initialized_container, sid)
    assert state.ledger == {"2026-07-23": 60, "2026-07-24": 60}
    assert state.total == 120
    assert sum(state.ledger.values()) == state.total


# -- quota truncation ---------------------------------------------------------


async def test_daily_quota_truncation_after_regression(client, initialized_container) -> None:
    """回退心跳不消耗额度；随后的截断精确到剩余每日额度。"""
    await _publish(client, daily_limit_seconds=100, session_limit_seconds=None, bedtime=None)
    sid = await _start(client)

    assert (await _hb(client, sid, 1, 60)).json()["extra"]["credited_seconds"] == 60
    assert (await _hb(client, sid, 2, 20)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    mid = await _snapshot(initialized_container, sid)
    assert sum(mid.ledger.values()) == 60  # regression consumed nothing

    resp = await _hb(client, sid, 3, 160)  # proposes 90, only 40 remain today
    body = resp.json()
    assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
    assert body["extra"]["limit_reason"] == "DENIED_DAILY_LIMIT_REACHED"
    assert body["extra"]["credited_seconds"] == 40

    state = await _snapshot(initialized_container, sid)
    assert state.total == 100
    assert sum(state.ledger.values()) == 100  # exactly the cap, never above
    assert state.status == "ENDED"
    assert state.outbox_events == ["session.started", "session.heartbeat", "session.ended"]


# -- concurrency --------------------------------------------------------------


@dataclass
class _FileEnv:
    client: AsyncClient
    container: Container
    db_file: Path


async def _make_file_env(db_file: Path, clock: FixedClock, id_prefix: str) -> _FileEnv:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_file}", heartbeat_max_gap_seconds=90
    )
    container = Container(settings=settings, clock=clock, ids=SequentialIdGenerator(id_prefix))
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
    app = create_app(container)
    app.state.container = container
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    await client.__aenter__()
    return _FileEnv(client=client, container=container, db_file=db_file)


async def _close_file_env(env: _FileEnv) -> None:
    await env.client.__aexit__(None, None, None)
    await env.container.dispose()


@pytest_asyncio.fixture
async def file_env(tmp_path, clock: FixedClock) -> AsyncIterator[_FileEnv]:
    env = await _make_file_env(tmp_path / "heartbeats.db", clock, "id")
    yield env
    await _close_file_env(env)


async def test_concurrent_heartbeats_settle_exactly_once(file_env: _FileEnv) -> None:
    """并发心跳：每个序号至多生效一次，账本/记录/发件箱与会话一致。"""
    client, container = file_env.client, file_env.container
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)

    results = await asyncio.gather(*[_hb(client, sid, seq, seq * 10) for seq in range(1, 9)])
    reasons = [r.json()["reason"] for r in results]
    assert all(r.status_code == 200 for r in results)
    assert set(reasons) <= {"HEARTBEAT_APPLIED", "HEARTBEAT_IGNORED_STALE"}
    applied = [r for r in results if r.json()["reason"] == "HEARTBEAT_APPLIED"]
    assert applied  # at least one beat must win

    # However the race serialised, the highest cumulative value is settled.
    state = await _snapshot(container, sid)
    assert state.total == 80
    assert state.marker == 80
    assert state.last_seq == 8
    assert sum(state.ledger.values()) == 80
    # Heartbeat rows and outbox events match the applied beats exactly.
    assert sum(credited for _, _, credited in state.heartbeats) == 80
    assert len(state.heartbeats) == len(applied)
    assert state.outbox_events.count("session.heartbeat") == len(applied)


async def test_concurrent_duplicate_heartbeats_credit_once(file_env: _FileEnv) -> None:
    """同一 (seq, 累计值) 的重试风暴只入账一次，且不产生 5xx。"""
    client, container = file_env.client, file_env.container
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)

    results = await asyncio.gather(*[_hb(client, sid, 1, 50) for _ in range(6)])
    bodies = [r.json() for r in results]
    assert all(r.status_code == 200 for r in results)
    applied = [b for b in bodies if b["reason"] == "HEARTBEAT_APPLIED"]
    stale = [b for b in bodies if b["reason"] == "HEARTBEAT_IGNORED_STALE"]
    assert len(applied) == 1
    assert len(stale) == 5
    assert applied[0]["extra"]["credited_seconds"] == 50

    state = await _snapshot(container, sid)
    assert state.total == 50
    assert sum(state.ledger.values()) == 50
    assert state.heartbeats == [(1, 50, 50)]
    assert state.outbox_events == ["session.started", "session.heartbeat"]


async def test_concurrent_regression_never_disturbs_valid_beats(file_env: _FileEnv) -> None:
    """回退心跳与合法心跳并发：回退恒被拒绝，合法心跳完整结算。"""
    client, container = file_env.client, file_env.container
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)
    assert (await _hb(client, sid, 1, 90)).json()["reason"] == "HEARTBEAT_APPLIED"

    regression, *valid = await asyncio.gather(
        _hb(client, sid, 2, 40),  # regressed cumulative value
        _hb(client, sid, 3, 150),
        _hb(client, sid, 4, 180),
    )
    assert regression.json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"
    assert {r.json()["reason"] for r in valid} <= {
        "HEARTBEAT_APPLIED",
        "HEARTBEAT_IGNORED_STALE",
    }

    # Every reachable jump stays within the per-beat gap cap, so the settled
    # total is deterministic regardless of how the race serialised.
    state = await _snapshot(container, sid)
    assert state.total == 180
    assert state.marker == 180
    assert state.last_seq == 4
    assert sum(state.ledger.values()) == 180
    seqs = [seq for seq, _, _ in state.heartbeats]
    assert seqs[0] == 1 and seqs[-1] == 4
    assert 2 not in seqs  # regression left no record
    assert sum(credited for _, _, credited in state.heartbeats) == 180


# -- restart recovery ---------------------------------------------------------


async def test_restart_recovery_preserves_baseline(tmp_path, clock: FixedClock) -> None:
    """进程重启后游标、基线与账本保持，回退仍被拒绝、合法心跳按原基线结算。"""
    db_file = tmp_path / "restart.db"

    env1 = await _make_file_env(db_file, clock, "a")
    await _publish(
        env1.client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None
    )
    sid = await _start(env1.client)
    assert (await _hb(env1.client, sid, 1, 90)).json()["reason"] == "HEARTBEAT_APPLIED"
    assert (await _hb(env1.client, sid, 2, 30)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"
    await _close_file_env(env1)

    # Simulate a process restart: a brand-new container on the same database.
    env2 = await _make_file_env(db_file, clock, "b")
    try:
        usage = await env2.client.get(f"/v1/sessions/{sid}/usage", headers=headers())
        assert usage.json()["session"]["total_watched_seconds"] == 90

        # Regression is still rejected against the recovered baseline...
        reg = (await _hb(env2.client, sid, 3, 60)).json()
        assert reg["reason"] == "HEARTBEAT_IGNORED_REGRESSION"
        assert reg["extra"]["watched_seconds_marker"] == 90

        # ...and a legitimate heartbeat settles from the preserved baseline.
        ok = (await _hb(env2.client, sid, 4, 150)).json()
        assert ok["reason"] == "HEARTBEAT_APPLIED"
        assert ok["extra"]["credited_seconds"] == 60

        state = await _snapshot(env2.container, sid)
        assert state.total == 150
        assert state.last_seq == 4
        assert sum(state.ledger.values()) == 150
        assert state.heartbeats == [(1, 90, 90), (4, 150, 60)]
    finally:
        await _close_file_env(env2)


# -- transactional consistency -------------------------------------------------


async def test_failed_heartbeat_rolls_back_all_stores(client, initialized_container, clock) -> None:
    """发件箱写入失败时，账本、心跳记录与会话游标在同一事务中整体回滚。"""
    await _publish(client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None)
    sid = await _start(client)
    container = initialized_container

    class FailingOutbox:
        async def add(self, event) -> None:
            raise RuntimeError("outbox boom")

    async with container.session_factory() as db:
        svc = SessionService(
            SqlSessionRepository(db),
            SqlPolicyRepository(db, clock_now=clock.now()),
            FailingOutbox(),
            container.clock,
            container.ids,
            ledger=SqlDailyUsageLedger(db),
            heartbeats=SqlHeartbeatRepository(db),
        )
        with pytest.raises(RuntimeError, match="outbox boom"):
            await svc.heartbeat(TENANT_A, sid, 1, 30)
        await db.rollback()  # mirrors the request-scoped rollback in get_db

    state = await _snapshot(container, sid)
    assert state.last_seq == 0
    assert state.marker == 0
    assert state.total == 0
    assert state.ledger == {}
    assert state.heartbeats == []
    assert state.outbox_events == ["session.started"]

    # The failed attempt left no residue: seq 1 is still usable.
    resp = await _hb(client, sid, 1, 30)
    assert resp.json()["reason"] == "HEARTBEAT_APPLIED"
    assert resp.json()["extra"]["credited_seconds"] == 30
