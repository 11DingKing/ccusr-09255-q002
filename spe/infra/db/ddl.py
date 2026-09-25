"""服务端业务模块。"""

from __future__ import annotations

ACTIVE_SESSION_INDEX = "uq_sessions_single_active"

CREATE_ACTIVE_SESSION_INDEX = (
    f"CREATE UNIQUE INDEX IF NOT EXISTS {ACTIVE_SESSION_INDEX} "
    "ON sessions (tenant_id, user_id) WHERE status != 'ENDED'"
)

DROP_ACTIVE_SESSION_INDEX = f"DROP INDEX IF EXISTS {ACTIVE_SESSION_INDEX}"
