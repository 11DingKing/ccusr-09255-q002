"""服务端业务模块。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from spe.domain.events import DomainEvent
from spe.domain.policy_ast import PolicyDocument
from spe.domain.repositories import PolicyRecord
from spe.domain.services.session_service import ActiveSessionExists
from spe.domain.session import Session
from spe.infra.db.models import (
    DailyUsageLedgerModel,
    HeartbeatModel,
    OutboxModel,
    PolicyModel,
    SessionModel,
)
from spe.infra.db.repositories.mappers import (
    PolicyRecordAdapter,
    apply_session_to_model,
    session_to_domain,
)


class SqlPolicyRepository:
    """封装领域状态与业务约束。"""

    def __init__(self, db: AsyncSession, clock_now: datetime) -> None:
        self._db = db
        self._now = clock_now

    async def next_version(self, tenant_id: str) -> int:
        stmt = select(func.max(PolicyModel.version)).where(PolicyModel.tenant_id == tenant_id)
        current = (await self._db.execute(stmt)).scalar()
        return (current or 0) + 1

    async def add(
        self, tenant_id: str, version: int, document: PolicyDocument, policy_id: str
    ) -> PolicyRecord:
        # New version becomes active; demote previous active versions.
        await self._db.execute(
            select(PolicyModel).where(
                PolicyModel.tenant_id == tenant_id, PolicyModel.is_active.is_(True)
            )
        )
        for prev in (
            await self._db.execute(
                select(PolicyModel).where(
                    PolicyModel.tenant_id == tenant_id, PolicyModel.is_active.is_(True)
                )
            )
        ).scalars():
            prev.is_active = False

        model = PolicyModel(
            id=policy_id,
            tenant_id=tenant_id,
            version=version,
            name=document.name,
            document=document.model_dump(mode="json"),
            is_active=True,
            created_at=self._now,
        )
        self._db.add(model)
        await self._db.flush()
        return PolicyRecordAdapter(model)

    async def get_active(self, tenant_id: str) -> PolicyRecord | None:
        stmt = (
            select(PolicyModel)
            .where(PolicyModel.tenant_id == tenant_id, PolicyModel.is_active.is_(True))
            .order_by(PolicyModel.version.desc())
            .limit(1)
        )
        model = (await self._db.execute(stmt)).scalar_one_or_none()
        return PolicyRecordAdapter(model) if model else None

    async def get_version(self, tenant_id: str, version: int) -> PolicyRecord | None:
        stmt = select(PolicyModel).where(
            PolicyModel.tenant_id == tenant_id, PolicyModel.version == version
        )
        model = (await self._db.execute(stmt)).scalar_one_or_none()
        return PolicyRecordAdapter(model) if model else None

    async def get_by_id(self, tenant_id: str, policy_id: str) -> PolicyRecord | None:
        stmt = select(PolicyModel).where(
            PolicyModel.tenant_id == tenant_id, PolicyModel.id == policy_id
        )
        model = (await self._db.execute(stmt)).scalar_one_or_none()
        return PolicyRecordAdapter(model) if model else None


class SqlSessionRepository:
    """封装领域状态与业务约束。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, session: Session, idempotency_key: str | None = None) -> None:
        model = SessionModel(
            id=session.id,
            tenant_id=session.tenant_id,
            user_id=session.user_id,
            policy_id=session.policy_id,
            policy_version=session.policy_version,
            status=session.status.value,
            idempotency_key=idempotency_key,
            birth_date=session.birth_date,
            started_at=session.started_at,
            updated_at=session.updated_at,
            ended_at=session.ended_at,
            last_seq=session.last_seq,
            watched_seconds_marker=session.watched_seconds_marker,
            total_watched_seconds=session.total_watched_seconds,
        )
        self._db.add(model)
        try:
            await self._db.flush()
        except IntegrityError as exc:  # single-active-session / idempotency guard tripped
            await self._db.rollback()
            raise ActiveSessionExists(str(exc)) from exc

    async def get(self, tenant_id: str, session_id: str) -> Session | None:
        stmt = select(SessionModel).where(
            SessionModel.tenant_id == tenant_id, SessionModel.id == session_id
        )
        model = (await self._db.execute(stmt)).scalar_one_or_none()
        return session_to_domain(model) if model else None

    async def get_for_update(self, tenant_id: str, session_id: str) -> Session | None:
        stmt = select(SessionModel).where(
            SessionModel.tenant_id == tenant_id, SessionModel.id == session_id
        )
        # Row-level lock so concurrent heartbeats for one session serialise.
        # SQLite has no row locks (single writer already serialises), so only
        # apply the FOR UPDATE clause on backends that support it.
        if self._db.bind is not None and self._db.bind.dialect.name != "sqlite":
            stmt = stmt.with_for_update()
        model = (await self._db.execute(stmt)).scalar_one_or_none()
        return session_to_domain(model) if model else None

    async def get_active_for_user(self, tenant_id: str, user_id: str) -> Session | None:
        stmt = select(SessionModel).where(
            SessionModel.tenant_id == tenant_id,
            SessionModel.user_id == user_id,
            SessionModel.status != "ENDED",
        )
        model = (await self._db.execute(stmt)).scalars().first()
        return session_to_domain(model) if model else None

    async def get_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> Session | None:
        stmt = select(SessionModel).where(
            SessionModel.tenant_id == tenant_id,
            SessionModel.idempotency_key == idempotency_key,
        )
        model = (await self._db.execute(stmt)).scalar_one_or_none()
        return session_to_domain(model) if model else None

    async def save(self, session: Session, idempotency_key: str | None = None) -> None:
        stmt = select(SessionModel).where(
            SessionModel.tenant_id == session.tenant_id, SessionModel.id == session.id
        )
        model = (await self._db.execute(stmt)).scalar_one()
        apply_session_to_model(session, model)
        if idempotency_key is not None:
            model.idempotency_key = idempotency_key
        await self._db.flush()


class SqlDailyUsageLedger:
    """封装领域状态与业务约束。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_seconds(self, tenant_id: str, user_id: str, local_day: str) -> int:
        stmt = select(DailyUsageLedgerModel.seconds).where(
            DailyUsageLedgerModel.tenant_id == tenant_id,
            DailyUsageLedgerModel.user_id == user_id,
            DailyUsageLedgerModel.local_day == local_day,
        )
        return (await self._db.execute(stmt)).scalar() or 0

    async def add_seconds(
        self, tenant_id: str, user_id: str, local_day: str, seconds: int
    ) -> int:
        if seconds <= 0:
            return await self.get_seconds(tenant_id, user_id, local_day)

        values = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "local_day": local_day,
            "seconds": seconds,
        }
        insert = sqlite_insert
        stmt = insert(DailyUsageLedgerModel).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "user_id", "local_day"],
            set_={"seconds": DailyUsageLedgerModel.seconds + seconds},
        )
        await self._db.execute(stmt)
        await self._db.flush()
        return await self.get_seconds(tenant_id, user_id, local_day)


class SqlOutboxRepository:
    """封装领域状态与业务约束。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(self, event: DomainEvent) -> None:
        self._db.add(
            OutboxModel(
                event_type=event.event_type,
                tenant_id=event.tenant_id,
                aggregate_id=event.aggregate_id,
                payload=event.payload,
                occurred_at=event.occurred_at,
                published=False,
            )
        )
        await self._db.flush()


class SqlHeartbeatRepository:
    """封装领域状态与业务约束。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        tenant_id: str,
        session_id: str,
        seq: int,
        watched_seconds_total: int,
        credited_seconds: int,
        occurred_at: datetime,
    ) -> None:
        self._db.add(
            HeartbeatModel(
                session_id=session_id,
                tenant_id=tenant_id,
                seq=seq,
                watched_seconds_total=watched_seconds_total,
                credited_seconds=credited_seconds,
                occurred_at=occurred_at,
            )
        )
        await self._db.flush()

    async def list_for_session(
        self, tenant_id: str, session_id: str
    ) -> list[HeartbeatModel]:
        stmt = (
            select(HeartbeatModel)
            .where(
                HeartbeatModel.tenant_id == tenant_id,
                HeartbeatModel.session_id == session_id,
            )
            .order_by(HeartbeatModel.seq)
        )
        return list((await self._db.execute(stmt)).scalars())
