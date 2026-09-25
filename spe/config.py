"""服务端业务模块。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """封装领域状态与业务约束。"""

    model_config = SettingsConfigDict(env_prefix="SPE_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./spe.db"
    """SQLAlchemy 异步 SQLite 连接地址。"""

    echo_sql: bool = False
    """When true, SQLAlchemy logs emitted SQL (development aid)."""

    heartbeat_max_gap_seconds: int = 90
    """Upper bound on watch-time credited by a single heartbeat interval.

    Guards against a client that goes silent then sends one huge heartbeat; the
    gap between two heartbeats is clamped to this many seconds.
    """

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """执行确定性的业务处理。"""
    return Settings()
