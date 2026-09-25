"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    """封装领域状态与业务约束。"""

    event_type: str
    tenant_id: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any]
