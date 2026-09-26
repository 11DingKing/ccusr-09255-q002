"""心跳顺序与累计标记联合校验的回归测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from spe.app import create_app
from spe.config import Settings
from spe.container import Container
from spe.domain.clock import FixedClock
from spe.domain.ids import SequentialIdGenerator
from spe.infra.db.base import Base
from spe.infra.db.ddl import CREATE_ACTIVE_SESSION_INDEX
from spe.infra.db.models import (
    DailyUsageLedgerModel,
    HeartbeatModel,
    OutboxModel,
    SessionModel,
)
from tests.conftest import birth_for_age, headers, sample_policy

pytestmark = pytest.mark.asyncio


async def _publish(client: AsyncClient, **kw) -> None:
    resp = await client.post(
        "/v1/policies", json={"document": sample_policy(**kw)}, headers=headers()
    )
    assert resp.status_code == 201


async def _start(client: AsyncClient, user_id: str = "u1", age: int = 20) -> str:
    resp = await client.post(
        "/v1/sessions",
        json={"user_id": user_id, "birth_date": birth_for_age(age).isoformat()},
        headers=headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]["id"]


async def _hb(client: AsyncClient, sid: str, seq: int, total: int):
    return await client.post(
        f"/v1/sessions/{sid}/heartbeat",
        json={"seq": seq, "watched_seconds_total": total},
        headers=headers(),
    )


async def _db_state(container: Container, sid: str):
    """Read the raw stores backing one session (same DB the app wrote)."""
    async with container.session_factory() as db:
        session_row = (
            await db.execute(select(SessionModel).where(SessionModel.id == sid))
        ).scalar_one()
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
        ledger_rows = (
            (await db.execute(select(DailyUsageLedgerModel))).scalars().all()
        )
        outbox_rows = (
            (
                await db.execute(
                    select(OutboxModel)
                    .where(OutboxModel.aggregate_id == sid)
                    .order_by(OutboxModel.id)
                )
            )
            .scalars()
            .all()
        )
    return session_row, hb_rows, ledger_rows, outbox_rows


@asynccontextmanager
async def _file_app(
    db_path: Path, clock: FixedClock
) -> AsyncIterator[tuple[AsyncClient, Container]]:
    """Build an app over a file-backed database, like a real process."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}", heartbeat_max_gap_seconds=90
    )
    container = Container(settings=settings, clock=clock, ids=SequentialIdGenerator("id"))
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
    app = create_app(container)
    app.state.container = container
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, container
    await container.dispose()


# -- regression guard ---------------------------------------------------------


async def test_regression_rejected_and_later_beat_settles_on_original_baseline(
    client, initialized_container
) -> None:
    await _publish(
        client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None
    )
    sid = await _start(client)

    assert (await _hb(client, sid, 1, 60)).json()["reason"] == "HEARTBEAT_APPLIED"
    assert (await _hb(client, sid, 2, 120)).json()["reason"] == "HEARTBEAT_APPLIED"

    # Reinstalled client: larger seq but a smaller cumulative total.
    resp = await _hb(client, sid, 3, 30)
    body = resp.json()
    assert resp.status_code == 200
    assert body["ok"] is False
    assert body["reason"] == "HEARTBEAT_IGNORED_REGRESSION"
    assert body["extra"]["watched_seconds_total"] == 30
    assert body["extra"]["watched_seconds_marker"] == 120
    assert body["extra"]["last_seq"] == 2

    # The regression wrote nothing: cursor, ledger, heartbeat log and outbox
    # are all exactly as they were after seq 2 (single-transaction consistency).
    session_row, hb_rows, ledger_rows, outbox_rows = await _db_state(
        initialized_container, sid
    )
    assert (
        session_row.last_seq,
        session_row.watched_seconds_marker,
        session_row.total_watched_seconds,
    ) == (2, 120, 120)
    assert [(r.seq, r.watched_seconds_total, r.credited_seconds) for r in hb_rows] == [
        (1, 60, 60),
        (2, 120, 60),
    ]
    assert sum(r.seconds for r in ledger_rows) == 120
    assert [r.event_type for r in outbox_rows] == [
        "session.started",
        "session.heartbeat",
        "session.heartbeat",
    ]

    # A legitimate heartbeat (the corrected client reuses seq 3, which the
    # regression never consumed) still settles against the original baseline.
    body = (await _hb(client, sid, 3, 150)).json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["credited_seconds"] == 30
    assert body["session"]["total_watched_seconds"] == 150


async def test_duplicate_and_zero_increment_have_distinguishable_results(
    client, initialized_container
) -> None:
    await _publish(
        client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None
    )
    sid = await _start(client)
    assert (await _hb(client, sid, 1, 60)).json()["reason"] == "HEARTBEAT_APPLIED"

    # Exact redelivery of seq 1 -> duplicate; new seq with the same cumulative
    # total -> genuine zero watch increment. Both must be distinguishable.
    dup = (await _hb(client, sid, 1, 60)).json()
    noop = (await _hb(client, sid, 2, 60)).json()

    assert dup["ok"] is False
    assert dup["reason"] == "HEARTBEAT_IGNORED_STALE"
    assert noop["ok"] is True
    assert noop["reason"] == "HEARTBEAT_APPLIED_NO_PROGRESS"
    assert noop["extra"]["credited_seconds"] == 0

    # The zero-increment beat advanced the cursor but credited nothing.
    session_row, hb_rows, ledger_rows, _ = await _db_state(initialized_container, sid)
    assert (
        session_row.last_seq,
        session_row.watched_seconds_marker,
        session_row.total_watched_seconds,
    ) == (2, 60, 60)
    assert [(r.seq, r.credited_seconds) for r in hb_rows] == [(1, 60), (2, 0)]
    assert sum(r.seconds for r in ledger_rows) == 60

    # The next real increment still settles against the same baseline.
    body = (await _hb(client, sid, 3, 100)).json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["credited_seconds"] == 40

    # Replay over the recorded beats reproduces the same settlement.
    replay = await client.get(f"/v1/sessions/{sid}/replay", headers=headers())
    steps = replay.json()["steps"]
    assert [s["seq"] for s in steps] == [1, 2, 3]
    assert [s["credited_seconds"] for s in steps] == [60, 0, 40]
    assert steps[-1]["total_watched_seconds"] == 100


# -- cross-midnight -----------------------------------------------------------


async def test_regression_rejected_across_midnight_boundary(
    client, initialized_container, clock
) -> None:
    # America/New_York: local midnight is 04:00 UTC (July, UTC-4).
    await _publish(
        client,
        timezone="America/New_York",
        daily_limit_seconds=80000,
        session_limit_seconds=80000,
        bedtime=None,
    )
    clock.set(datetime(2026, 7, 24, 3, 59, 0, tzinfo=UTC))  # 23:59:00 local
    sid = await _start(client)

    # A 60s beat ending at 00:00:30 local straddles midnight.
    clock.set(datetime(2026, 7, 24, 4, 0, 30, tzinfo=UTC))
    body = (await _hb(client, sid, 1, 60)).json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["per_day"] == {"2026-07-23": 30, "2026-07-24": 30}

    # Past midnight, a regressed cumulative total is still rejected...
    clock.set(datetime(2026, 7, 24, 4, 1, 0, tzinfo=UTC))  # 00:01:00 local
    assert (await _hb(client, sid, 2, 20)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    # ...and the legitimate next beat (90s proposed, exactly the max gap) still
    # splits across the two local days against the untouched baseline.
    body = (await _hb(client, sid, 2, 150)).json()
    assert body["reason"] == "HEARTBEAT_APPLIED"
    assert body["extra"]["per_day"] == {"2026-07-23": 30, "2026-07-24": 60}

    _, hb_rows, ledger_rows, _ = await _db_state(initialized_container, sid)
    assert [r.seq for r in hb_rows] == [1, 2]
    assert {r.local_day: r.seconds for r in ledger_rows} == {
        "2026-07-23": 60,
        "2026-07-24": 90,
    }


# -- concurrency --------------------------------------------------------------


async def test_concurrent_duplicate_heartbeats_apply_exactly_once(file_client) -> None:
    await _publish(
        file_client, session_limit_seconds=86400, daily_limit_seconds=86400, bedtime=None
    )
    sid = await _start(file_client)

    results = await asyncio.gather(*[_hb(file_client, sid, 1, 30) for _ in range(8)])
    assert all(r.status_code == 200 for r in results)
    reasons = [r.json()["reason"] for r in results]
    assert reasons.count("HEARTBEAT_APPLIED") == 1
    assert reasons.count("HEARTBEAT_IGNORED_STALE") == 7

    usage = await file_client.get(f"/v1/sessions/{sid}/usage", headers=headers())
    assert usage.json()["session"]["total_watched_seconds"] == 30
    assert usage.json()["extra"]["daily_today_seconds"] == 30


async def test_concurrent_heartbeats_keep_ledger_records_and_outbox_consistent(
    tmp_path, clock
) -> None:
    async with _file_app(tmp_path / "storm.db", clock) as (client, container):
        await _publish(
            client, session_limit_seconds=86400, daily_limit_seconds=86400, bedtime=None
        )
        sid = await _start(client)
        assert (await _hb(client, sid, 1, 30)).json()["reason"] == "HEARTBEAT_APPLIED"

        # Distinct increasing beats race each other; serialization order is
        # unspecified, but every request must resolve cleanly.
        results = await asyncio.gather(*[_hb(client, sid, i, i * 30) for i in range(2, 10)])
        assert all(r.status_code == 200 for r in results)
        reasons = {r.json()["reason"] for r in results}
        assert reasons <= {"HEARTBEAT_APPLIED", "HEARTBEAT_IGNORED_STALE"}

        # However the race serialised, the three stores tell one story.
        session_row, hb_rows, ledger_rows, outbox_rows = await _db_state(container, sid)
        credited_sum = sum(r.credited_seconds for r in hb_rows)
        assert session_row.total_watched_seconds == credited_sum
        assert sum(r.seconds for r in ledger_rows) == credited_sum
        assert session_row.last_seq == max(r.seq for r in hb_rows)
        assert session_row.watched_seconds_marker == max(
            r.watched_seconds_total for r in hb_rows
        )
        hb_events = [r for r in outbox_rows if r.event_type == "session.heartbeat"]
        assert len(hb_events) == len(hb_rows)


async def test_concurrent_regression_never_wins(tmp_path, clock) -> None:
    async with _file_app(tmp_path / "race.db", clock) as (client, container):
        await _publish(
            client, session_limit_seconds=86400, daily_limit_seconds=86400, bedtime=None
        )
        sid = await _start(client)
        assert (await _hb(client, sid, 1, 90)).json()["reason"] == "HEARTBEAT_APPLIED"

        # A legitimate beat and a regressed beat arrive concurrently; either
        # serialization must produce the same outcome.
        legit, regress = await asyncio.gather(
            _hb(client, sid, 2, 150), _hb(client, sid, 3, 10)
        )
        assert legit.json()["reason"] == "HEARTBEAT_APPLIED"
        assert legit.json()["extra"]["credited_seconds"] == 60
        assert regress.json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

        session_row, hb_rows, ledger_rows, _ = await _db_state(container, sid)
        assert (session_row.last_seq, session_row.watched_seconds_marker) == (2, 150)
        assert [r.seq for r in hb_rows] == [1, 2]
        assert sum(r.seconds for r in ledger_rows) == 150


# -- quota truncation ---------------------------------------------------------


async def test_regression_does_not_consume_daily_budget(
    client, initialized_container
) -> None:
    # No session cap: the daily budget alone bounds the credit.
    await _publish(
        client, daily_limit_seconds=100, session_limit_seconds=None, bedtime=None
    )
    sid = await _start(client)
    assert (await _hb(client, sid, 1, 60)).json()["extra"]["credited_seconds"] == 60

    # Rejected regression: must not eat into the daily budget.
    assert (await _hb(client, sid, 2, 10)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    # 90s proposed but only 40s of daily budget left -> truncated, session ends.
    body = (await _hb(client, sid, 2, 150)).json()
    assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
    assert body["extra"]["limit_reason"] == "DENIED_DAILY_LIMIT_REACHED"
    assert body["extra"]["credited_seconds"] == 40

    session_row, hb_rows, ledger_rows, outbox_rows = await _db_state(
        initialized_container, sid
    )
    assert sum(r.seconds for r in ledger_rows) == 100
    assert session_row.total_watched_seconds == 100
    assert [(r.seq, r.credited_seconds) for r in hb_rows] == [(1, 60), (2, 40)]
    assert [r.event_type for r in outbox_rows] == [
        "session.started",
        "session.heartbeat",
        "session.ended",
    ]


async def test_regression_does_not_consume_session_budget(
    client, initialized_container
) -> None:
    await _publish(
        client, session_limit_seconds=50, daily_limit_seconds=10000, bedtime=None
    )
    sid = await _start(client)
    assert (await _hb(client, sid, 1, 30)).json()["extra"]["credited_seconds"] == 30
    assert (await _hb(client, sid, 2, 5)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

    body = (await _hb(client, sid, 2, 80)).json()  # proposes 50, only 20 left
    assert body["reason"] == "SESSION_ENDED_BY_LIMIT"
    assert body["extra"]["limit_reason"] == "DENIED_SESSION_LIMIT_REACHED"
    assert body["extra"]["credited_seconds"] == 20
    assert body["session"]["total_watched_seconds"] == 50


# -- restart recovery ---------------------------------------------------------


async def test_cursor_and_baseline_survive_restart(tmp_path, clock) -> None:
    db_path = tmp_path / "restart.db"
    async with _file_app(db_path, clock) as (client, _container):
        await _publish(
            client, session_limit_seconds=10000, daily_limit_seconds=10000, bedtime=None
        )
        sid = await _start(client)
        assert (await _hb(client, sid, 1, 60)).json()["reason"] == "HEARTBEAT_APPLIED"
        assert (await _hb(client, sid, 2, 120)).json()["reason"] == "HEARTBEAT_APPLIED"

    # "Restart": a brand-new container/engine over the same database file.
    async with _file_app(db_path, clock) as (client, container):
        # The restored cursor still rejects stale and regressed beats...
        assert (await _hb(client, sid, 2, 120)).json()["reason"] == "HEARTBEAT_IGNORED_STALE"
        assert (await _hb(client, sid, 3, 40)).json()["reason"] == "HEARTBEAT_IGNORED_REGRESSION"

        # ...and a legitimate beat settles against the restored baseline.
        body = (await _hb(client, sid, 3, 180)).json()
        assert body["reason"] == "HEARTBEAT_APPLIED"
        assert body["extra"]["credited_seconds"] == 60

        session_row, hb_rows, ledger_rows, _ = await _db_state(container, sid)
        assert (
            session_row.last_seq,
            session_row.watched_seconds_marker,
            session_row.total_watched_seconds,
        ) == (3, 180, 180)
        assert [r.seq for r in hb_rows] == [1, 2, 3]
        assert sum(r.seconds for r in ledger_rows) == 180
