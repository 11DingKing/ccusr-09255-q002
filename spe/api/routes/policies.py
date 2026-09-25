"""服务端业务模块。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from spe.api.deps import Svc, TenantId
from spe.api.schemas.http import (
    CheckPolicyResponse,
    PreviewRequest,
    PreviewResponse,
    PublishPolicyRequest,
    PublishPolicyResponse,
    ValidationIssueOut,
)
from spe.domain.reason_codes import ReasonCode
from spe.domain.services.policy_service import PolicyValidationError

router = APIRouter(prefix="/v1/policies", tags=["policies"])


@router.post("/check", response_model=CheckPolicyResponse)
async def check_policy(body: PublishPolicyRequest, svc: Svc) -> CheckPolicyResponse:
    """执行确定性的业务处理。"""
    result = svc.policy_service.validate(body.document)
    return CheckPolicyResponse(
        ok=result.ok,
        issues=[ValidationIssueOut(path=i.path, message=i.message) for i in result.issues],
    )


@router.post("", response_model=PublishPolicyResponse, status_code=status.HTTP_201_CREATED)
async def publish_policy(
    body: PublishPolicyRequest, tenant_id: TenantId, svc: Svc
) -> PublishPolicyResponse:
    """执行确定性的业务处理。"""
    try:
        published = await svc.policy_service.publish(tenant_id, body.document)
    except PolicyValidationError as exc:
        raise HTTPException(
            status_code=422,  # Unprocessable Content
            detail={
                "reason": ReasonCode.REJECTED_POLICY_INVALID.value,
                "issues": [
                    {"path": i.path, "message": i.message} for i in exc.result.issues
                ],
            },
        ) from exc
    return PublishPolicyResponse(
        policy_id=published.policy_id,
        tenant_id=published.tenant_id,
        version=published.version,
        name=published.name,
    )


@router.post("/preview", response_model=PreviewResponse)
async def preview_policy(
    body: PreviewRequest, tenant_id: TenantId, svc: Svc
) -> PreviewResponse:
    """执行确定性的业务处理。"""
    now = body.at or svc.clock.now()
    result = await svc.policy_service.preview(
        tenant_id,
        user_id=body.user_id,
        birth_date=body.birth_date,
        now=now,
        daily_usage_seconds=body.daily_usage_seconds,
        session_elapsed_seconds=body.session_elapsed_seconds,
        version=body.version,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reason": ReasonCode.REJECTED_POLICY_NOT_FOUND.value},
        )
    return PreviewResponse(allowed=result.allowed, reason=result.reason, trace=result.trace)
