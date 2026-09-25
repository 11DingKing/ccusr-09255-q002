"""服务端业务模块。"""

from __future__ import annotations

from typing import Protocol

from spe.domain.events import DomainEvent
from spe.domain.policy_ast import PolicyDocument
from spe.domain.session import Session


class PolicyRecord(Protocol):
    """封装领域状态与业务约束。"""

    @property
    def id(self) -> str: ...
    @property
    def tenant_id(self) -> str: ...
    @property
    def version(self) -> int: ...
    @property
    def document(self) -> PolicyDocument: ...


class PolicyRepository(Protocol):
    """封装领域状态与业务约束。"""

    async def next_version(self, tenant_id: str) -> int:
        """执行确定性的业务处理。"""
        ...

    async def add(
        self, tenant_id: str, version: int, document: PolicyDocument, policy_id: str
    ) -> PolicyRecord:
        """执行确定性的业务处理。"""
        ...

    async def get_active(self, tenant_id: str) -> PolicyRecord | None:
        """执行确定性的业务处理。"""
        ...

    async def get_version(self, tenant_id: str, version: int) -> PolicyRecord | None:
        """执行确定性的业务处理。"""
        ...

    async def get_by_id(self, tenant_id: str, policy_id: str) -> PolicyRecord | None:
        """执行确定性的业务处理。"""
        ...


class SessionRepository(Protocol):
    """封装领域状态与业务约束。"""

    async def add(self, session: Session) -> None: ...

    async def get(self, tenant_id: str, session_id: str) -> Session | None: ...

    async def get_for_update(
        self, tenant_id: str, session_id: str
    ) -> Session | None:
        """执行确定性的业务处理。"""
        ...

    async def get_active_for_user(self, tenant_id: str, user_id: str) -> Session | None: ...

    async def get_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> Session | None: ...

    async def save(self, session: Session, idempotency_key: str | None = None) -> None: ...


class DailyUsageLedger(Protocol):
    """封装领域状态与业务约束。"""

    async def get_seconds(self, tenant_id: str, user_id: str, local_day: str) -> int:
        """执行确定性的业务处理。"""
        ...

    async def add_seconds(
        self, tenant_id: str, user_id: str, local_day: str, seconds: int
    ) -> int:
        """执行确定性的业务处理。"""
        ...


class OutboxRepository(Protocol):
    """封装领域状态与业务约束。"""

    async def add(self, event: DomainEvent) -> None: ...


class HeartbeatSink(Protocol):
    """封装领域状态与业务约束。"""

    async def record(
        self,
        tenant_id: str,
        session_id: str,
        seq: int,
        watched_seconds_total: int,
        credited_seconds: int,
        occurred_at: object,
    ) -> None: ...
