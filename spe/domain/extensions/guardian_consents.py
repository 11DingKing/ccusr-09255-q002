"""服务端业务模块。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Iterable, Mapping, Protocol, Sequence


class State(StrEnum):
    REQUESTED = "requested"
    VERIFIED = "verified"
    GRANTED = "granted"
    REVOKED = "revoked"


ALLOWED_TRANSITIONS: Mapping[State, frozenset[State]] = {
    State.REQUESTED: frozenset({State.VERIFIED, State.GRANTED}),
    State.VERIFIED: frozenset({State.GRANTED, State.REVOKED}),
    State.GRANTED: frozenset({State.REVOKED}),
    State.REVOKED: frozenset({}),
}


class DomainError(ValueError):
    """封装领域状态与业务约束。"""


@dataclass(frozen=True)
class AuditEntry:
    sequence: int
    action: str
    actor_id: str
    occurred_at: datetime
    before: str
    after: str
    reason: str
    fingerprint: str


@dataclass(frozen=True)
class Record:
    consent_id: str
    owner_id: str
    state: State
    version: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    attributes: Mapping[str, str] = field(default_factory=dict)
    audit: tuple[AuditEntry, ...] = ()

    @property
    def identifier(self) -> str:
        return self.consent_id

    def expired(self, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at


class Repository(Protocol):
    def get(self, identifier: str) -> Record | None: ...
    def save(self, record: Record, expected_version: int) -> None: ...
    def list_for_owner(self, owner_id: str) -> Sequence[Record]: ...


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DomainError("时间必须包含时区")
    return value.astimezone(UTC)


def _fingerprint(identifier: str, version: int, action: str, actor: str, reason: str) -> str:
    raw = f"{identifier}|{version}|{action}|{actor}|{reason}".encode("utf-8")
    return sha256(raw).hexdigest()


def create_record(
    identifier: str,
    owner_id: str,
    now: datetime,
    *,
    ttl: timedelta | None = None,
    attributes: Mapping[str, str] | None = None,
) -> Record:
    identifier = identifier.strip()
    owner_id = owner_id.strip()
    if not identifier or not owner_id:
        raise DomainError("标识和归属方不能为空")
    instant = _utc(now)
    expiry = instant + ttl if ttl is not None else None
    if ttl is not None and ttl <= timedelta(0):
        raise DomainError("有效期必须大于零")
    return Record(
        consent_id=identifier,
        owner_id=owner_id,
        state=State.REQUESTED,
        version=1,
        created_at=instant,
        updated_at=instant,
        expires_at=expiry,
        attributes=dict(attributes or {}),
    )


def transition(record: Record, target: State, actor_id: str, now: datetime, reason: str) -> Record:
    instant = _utc(now)
    actor = actor_id.strip()
    reason = reason.strip()
    if not actor or not reason:
        raise DomainError("状态变更必须记录操作人和原因")
    if record.expired(instant) and target not in {State.REVOKED}:
        raise DomainError("已过期记录只能进入终态")
    if target == record.state:
        return record
    if target not in ALLOWED_TRANSITIONS.get(record.state, frozenset()):
        raise DomainError(f"不允许从 {record.state} 变更到 {target}")
    next_version = record.version + 1
    entry = AuditEntry(
        sequence=len(record.audit) + 1,
        action="transition",
        actor_id=actor,
        occurred_at=instant,
        before=record.state.value,
        after=target.value,
        reason=reason,
        fingerprint=_fingerprint(record.identifier, next_version, target.value, actor, reason),
    )
    return replace(
        record,
        state=target,
        version=next_version,
        updated_at=instant,
        audit=record.audit + (entry,),
    )


def patch_attributes(
    record: Record,
    changes: Mapping[str, str | None],
    actor_id: str,
    now: datetime,
    reason: str,
) -> Record:
    actor = actor_id.strip()
    note = reason.strip()
    if not actor or not note:
        raise DomainError("修改属性必须保留审计说明")
    merged = dict(record.attributes)
    for key, value in changes.items():
        normalized = key.strip()
        if not normalized:
            raise DomainError("属性名不能为空")
        if value is None:
            merged.pop(normalized, None)
        else:
            merged[normalized] = value.strip()
    instant = _utc(now)
    next_version = record.version + 1
    entry = AuditEntry(
        sequence=len(record.audit) + 1,
        action="patch",
        actor_id=actor,
        occurred_at=instant,
        before=str(sorted(record.attributes.items())),
        after=str(sorted(merged.items())),
        reason=note,
        fingerprint=_fingerprint(record.identifier, next_version, "patch", actor, note),
    )
    return replace(
        record,
        attributes=merged,
        version=next_version,
        updated_at=instant,
        audit=record.audit + (entry,),
    )


def verify_audit(record: Record) -> bool:
    expected = 1
    seen: set[str] = set()
    for entry in record.audit:
        if entry.sequence != expected or entry.fingerprint in seen:
            return False
        seen.add(entry.fingerprint)
        expected += 1
    return True


def latest(records: Iterable[Record]) -> dict[str, Record]:
    result: dict[str, Record] = {}
    for record in records:
        current = result.get(record.identifier)
        if current is None or (record.version, record.updated_at) > (current.version, current.updated_at):
            result[record.identifier] = record
    return result


def summarize(records: Iterable[Record], now: datetime) -> dict[str, int]:
    instant = _utc(now)
    counts = {state.value: 0 for state in State}
    counts["expired"] = 0
    counts["invalid_audit"] = 0
    for record in latest(records).values():
        counts[record.state.value] += 1
        if record.expired(instant):
            counts["expired"] += 1
        if not verify_audit(record):
            counts["invalid_audit"] += 1
    return counts
