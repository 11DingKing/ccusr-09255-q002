"""服务端业务模块。"""

from __future__ import annotations

from fastapi import APIRouter

from spe.api.deps import Db
from spe.infra.db.outbox import relay_once

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/outbox/relay")
async def relay(db: Db) -> dict[str, int]:
    """执行确定性的业务处理。"""
    sent = await relay_once(db)
    return {"published": sent}
