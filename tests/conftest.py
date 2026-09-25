"""服务端业务模块。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from spe.app import create_app
from spe.config import Settings
from spe.container import Container
from spe.domain.clock import FixedClock
from spe.domain.ids import SequentialIdGenerator
from spe.infra.db.base import Base
from spe.infra.db.ddl import CREATE_ACTIVE_SESSION_INDEX

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

# A fixed reference instant: 2026-07-24 10:00 UTC.
REFERENCE = datetime(2026, 7, 24, 10, 0, 0, tzinfo=UTC)

def birth_for_age(age: int, ref: datetime = REFERENCE) -> date:
    """执行确定性的业务处理。"""
    d = ref.date()
    return date(d.year - age, d.month, d.day)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(REFERENCE)


@pytest.fixture
def container(clock: FixedClock) -> Container:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", heartbeat_max_gap_seconds=90)
    return Container(settings=settings, clock=clock, ids=SequentialIdGenerator("id"))


@pytest_asyncio.fixture
async def initialized_container(container: Container) -> AsyncIterator[Container]:
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
    yield container
    await container.dispose()


@pytest_asyncio.fixture
async def client(initialized_container: Container) -> AsyncIterator[AsyncClient]:
    # Build the app but bypass its lifespan (which would dispose the container);
    # the initialized_container fixture owns the engine lifecycle here.
    app = create_app(initialized_container)
    app.state.container = initialized_container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def file_client(tmp_path, clock: FixedClock) -> AsyncIterator[AsyncClient]:
    """执行确定性的业务处理。"""
    db_file = tmp_path / "concurrency.db"
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_file}", heartbeat_max_gap_seconds=90
    )
    container = Container(settings=settings, clock=clock, ids=SequentialIdGenerator("id"))
    async with container.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(CREATE_ACTIVE_SESSION_INDEX))
    app = create_app(container)
    app.state.container = container
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await container.dispose()


def headers(tenant_id: str = TENANT_A) -> dict[str, str]:
    return {"X-Tenant-ID": tenant_id}


def sample_policy(
    name: str = "default",
    timezone: str = "UTC",
    min_age: int | None = 13,
    daily_limit_seconds: int | None = 3600,
    session_limit_seconds: int | None = 1800,
    bedtime: tuple[str, str] | None = ("22:00:00", "06:00:00"),
    exceptions: list[dict] | None = None,
) -> dict:
    """执行确定性的业务处理。"""
    rules: dict = {"timezone": timezone, "exceptions": exceptions or []}
    if min_age is not None:
        rules["age_gate"] = {"kind": "age_gate", "min_age": min_age}
    if daily_limit_seconds is not None:
        rules["daily_limit"] = {"kind": "daily_limit", "max_seconds": daily_limit_seconds}
    if session_limit_seconds is not None:
        rules["session_limit"] = {"kind": "session_limit", "max_seconds": session_limit_seconds}
    if bedtime is not None:
        rules["bedtime"] = {
            "kind": "bedtime",
            "windows": [{"start": bedtime[0], "end": bedtime[1]}],
        }
    return {"name": name, "rules": rules}
