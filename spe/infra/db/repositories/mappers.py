"""服务端业务模块。"""

from __future__ import annotations

from spe.domain.policy_ast import PolicyDocument
from spe.domain.session import Session, SessionStatus
from spe.infra.db.models import PolicyModel, SessionModel


class PolicyRecordAdapter:
    """封装领域状态与业务约束。"""

    def __init__(self, model: PolicyModel) -> None:
        self._model = model
        self._document = PolicyDocument.model_validate(model.document)

    @property
    def id(self) -> str:
        return self._model.id

    @property
    def tenant_id(self) -> str:
        return self._model.tenant_id

    @property
    def version(self) -> int:
        return self._model.version

    @property
    def document(self) -> PolicyDocument:
        return self._document


def session_to_domain(model: SessionModel) -> Session:
    return Session(
        id=model.id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        policy_id=model.policy_id,
        policy_version=model.policy_version,
        status=SessionStatus(model.status),
        birth_date=model.birth_date,
        started_at=model.started_at,
        updated_at=model.updated_at,
        ended_at=model.ended_at,
        last_seq=model.last_seq,
        watched_seconds_marker=model.watched_seconds_marker,
        total_watched_seconds=model.total_watched_seconds,
    )


def apply_session_to_model(session: Session, model: SessionModel) -> None:
    model.status = session.status.value
    model.updated_at = session.updated_at
    model.ended_at = session.ended_at
    model.last_seq = session.last_seq
    model.watched_seconds_marker = session.watched_seconds_marker
    model.total_watched_seconds = session.total_watched_seconds
