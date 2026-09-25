"""服务端业务模块。"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from spe.api.routes import admin, policies, sessions
from spe.container import Container


def create_app(container: Container | None = None) -> FastAPI:
    """执行确定性的业务处理。"""
    container = container or Container()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container
        yield
        await container.dispose()

    app = FastAPI(
        title="Shortfeed Policy Engine",
        version="0.1.0",
        summary="Versioned, multi-tenant short-video session policy engine.",
        lifespan=lifespan,
    )
    app.include_router(policies.router)
    app.include_router(sessions.router)
    app.include_router(admin.router)
    return app


app = create_app()
