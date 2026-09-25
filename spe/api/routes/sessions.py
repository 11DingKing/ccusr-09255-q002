"""服务端业务模块。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from spe.api.deps import Svc, TenantId
from spe.api.schemas.http import (
    ActionResponse,
    HeartbeatRequest,
    ReplayResponse,
    ReplayStepOut,
    SessionOut,
    StartSessionRequest,
)
from spe.domain.reason_codes import ReasonCode
from spe.domain.services.replay_service import HeartbeatRecord, replay_session
from spe.domain.services.responses import ActionResult

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])

# Reason codes that map to specific HTTP statuses when an action is not ``ok``.
_NOT_FOUND = {ReasonCode.REJECTED_SESSION_NOT_FOUND, ReasonCode.REJECTED_POLICY_NOT_FOUND}
_CONFLICT = {ReasonCode.REJECTED_ACTIVE_SESSION_EXISTS}


def _to_response(result: ActionResult) -> ActionResponse:
    session = SessionOut(**result.session.__dict__) if result.session else None
    return ActionResponse(
        ok=result.ok,
        reason=result.reason,
        session=session,
        trace=result.trace,  # type: ignore[arg-type]
        extra=result.extra,
    )


def _status_for(result: ActionResult) -> int:
    if result.ok:
        return status.HTTP_200_OK
    if result.reason in _NOT_FOUND:
        return status.HTTP_404_NOT_FOUND
    if result.reason in _CONFLICT:
        return status.HTTP_409_CONFLICT
    return status.HTTP_200_OK


@router.post("", response_model=ActionResponse)
async def start_session(
    body: StartSessionRequest, tenant_id: TenantId, svc: Svc
) -> ActionResponse:
    result = await svc.session_service.start(
        tenant_id=tenant_id,
        user_id=body.user_id,
        birth_date=body.birth_date,
        idempotency_key=body.idempotency_key,
    )
    _raise_if_http_error(result)
    return _to_response(result)


@router.post("/{session_id}/heartbeat", response_model=ActionResponse)
async def heartbeat(
    session_id: str, body: HeartbeatRequest, tenant_id: TenantId, svc: Svc
) -> ActionResponse:
    result = await svc.session_service.heartbeat(
        tenant_id=tenant_id,
        session_id=session_id,
        seq=body.seq,
        watched_seconds_total=body.watched_seconds_total,
    )
    _raise_if_http_error(result)
    return _to_response(result)


@router.post("/{session_id}/pause", response_model=ActionResponse)
async def pause(session_id: str, tenant_id: TenantId, svc: Svc) -> ActionResponse:
    result = await svc.session_service.pause(tenant_id, session_id)
    _raise_if_http_error(result)
    return _to_response(result)


@router.post("/{session_id}/resume", response_model=ActionResponse)
async def resume(session_id: str, tenant_id: TenantId, svc: Svc) -> ActionResponse:
    result = await svc.session_service.resume(tenant_id, session_id)
    _raise_if_http_error(result)
    return _to_response(result)


@router.post("/{session_id}/end", response_model=ActionResponse)
async def end(session_id: str, tenant_id: TenantId, svc: Svc) -> ActionResponse:
    result = await svc.session_service.end(tenant_id, session_id)
    _raise_if_http_error(result)
    return _to_response(result)


@router.get("/{session_id}/usage", response_model=ActionResponse)
async def usage(session_id: str, tenant_id: TenantId, svc: Svc) -> ActionResponse:
    result = await svc.session_service.get_usage(tenant_id, session_id)
    _raise_if_http_error(result)
    return _to_response(result)


@router.get("/{session_id}/replay", response_model=ReplayResponse)
async def replay(session_id: str, tenant_id: TenantId, svc: Svc) -> ReplayResponse:
    """执行确定性的业务处理。"""
    session_result = await svc.session_service.get_usage(tenant_id, session_id)
    if not session_result.ok or session_result.session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": ReasonCode.REJECTED_SESSION_NOT_FOUND.value},
        )

    domain_session = await svc.session_service._sessions.get(  # noqa: SLF001
        tenant_id, session_id
    )
    assert domain_session is not None
    policy = await svc.policies.get_by_id(tenant_id, domain_session.policy_id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": ReasonCode.REJECTED_POLICY_NOT_FOUND.value},
        )

    rows = await svc.heartbeats.list_for_session(tenant_id, session_id)
    records = [
        HeartbeatRecord(
            occurred_at=r.occurred_at, seq=r.seq, watched_seconds_total=r.watched_seconds_total
        )
        for r in rows
    ]
    steps = replay_session(domain_session, policy.document, records)
    return ReplayResponse(
        session_id=session_id,
        policy_version=domain_session.policy_version,
        steps=[ReplayStepOut(**step.__dict__) for step in steps],  # type: ignore[arg-type]
    )


def _raise_if_http_error(result: ActionResult) -> None:
    """执行确定性的业务处理。"""
    if result.ok:
        return
    if result.reason in _NOT_FOUND:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": result.reason.value},
        )
    if result.reason in _CONFLICT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "reason": result.reason.value,
                "session_id": result.session.id if result.session else None,
            },
        )
