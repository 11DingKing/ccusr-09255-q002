"""服务端业务模块。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spe.infra.db.models import OutboxModel

Publisher = Callable[[OutboxModel], Awaitable[None]]


async def _log_publisher(event: OutboxModel) -> None:
    # Placeholder sink; replace with a real broker client in production.
    return None


async def relay_once(
    db: AsyncSession, publisher: Publisher = _log_publisher, batch_size: int = 100
) -> int:
    """执行确定性的业务处理。"""
    stmt = (
        select(OutboxModel)
        .where(OutboxModel.published.is_(False))
        .order_by(OutboxModel.id)
        .limit(batch_size)
    )
    rows = list((await db.execute(stmt)).scalars())
    for row in rows:
        await publisher(row)
        row.published = True
    await db.flush()
    return len(rows)


def event_to_dict(row: OutboxModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "tenant_id": row.tenant_id,
        "aggregate_id": row.aggregate_id,
        "payload": row.payload,
        "occurred_at": row.occurred_at.isoformat(),
    }
