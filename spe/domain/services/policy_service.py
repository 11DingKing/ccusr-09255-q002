"""服务端业务模块。"""

from __future__ import annotations

from datetime import date, datetime

from spe.domain.clock import Clock
from spe.domain.events import DomainEvent
from spe.domain.ids import IdGenerator
from spe.domain.policy_ast import PolicyDocument
from spe.domain.policy_interpreter import EvalContext, evaluate
from spe.domain.policy_validator import ValidationResult, validate_policy
from spe.domain.repositories import OutboxRepository, PolicyRepository
from spe.domain.services.responses import PolicyPublished, PreviewResult
from spe.domain.timeutil import age_at


class PolicyValidationError(Exception):
    """封装领域状态与业务约束。"""

    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        super().__init__("policy failed static validation")


class PolicyService:
    """封装领域状态与业务约束。"""

    def __init__(
        self,
        policies: PolicyRepository,
        outbox: OutboxRepository,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._policies = policies
        self._outbox = outbox
        self._clock = clock
        self._ids = ids

    def validate(self, document: PolicyDocument) -> ValidationResult:
        """执行确定性的业务处理。"""
        return validate_policy(document)

    async def publish(self, tenant_id: str, document: PolicyDocument) -> PolicyPublished:
        """执行确定性的业务处理。"""
        result = validate_policy(document)
        if not result.ok:
            raise PolicyValidationError(result)

        version = await self._policies.next_version(tenant_id)
        policy_id = self._ids.new_id()
        record = await self._policies.add(tenant_id, version, document, policy_id)

        await self._outbox.add(
            DomainEvent(
                event_type="policy.published",
                tenant_id=tenant_id,
                aggregate_id=record.id,
                occurred_at=self._clock.now(),
                payload={"policy_id": record.id, "version": version, "name": document.name},
            )
        )
        return PolicyPublished(
            policy_id=record.id, tenant_id=tenant_id, version=version, name=document.name
        )

    async def preview(
        self,
        tenant_id: str,
        *,
        user_id: str,
        birth_date: date,
        now: datetime,
        daily_usage_seconds: int = 0,
        session_elapsed_seconds: int = 0,
        version: int | None = None,
    ) -> PreviewResult | None:
        """执行确定性的业务处理。"""
        record = (
            await self._policies.get_version(tenant_id, version)
            if version is not None
            else await self._policies.get_active(tenant_id)
        )
        if record is None:
            return None
        tz = record.document.rules.timezone
        ctx = EvalContext(
            now=now,
            user_id=user_id,
            user_age=age_at(birth_date, now, tz),
            daily_usage_seconds=daily_usage_seconds,
            session_elapsed_seconds=session_elapsed_seconds,
        )
        decision = evaluate(record.document, ctx)
        return PreviewResult(
            allowed=decision.allowed,
            reason=decision.reason,
            trace=decision.trace.as_list(),
        )

    async def get_active_version(self, tenant_id: str) -> int | None:
        record = await self._policies.get_active(tenant_id)
        return record.version if record else None
