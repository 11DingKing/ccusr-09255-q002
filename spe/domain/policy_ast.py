"""服务端业务模块。"""

from __future__ import annotations

from datetime import time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

# --- Reusable constrained types --------------------------------------------

Minutes = Annotated[int, Field(ge=0, le=24 * 60)]
"""A duration expressed in whole minutes within a single day."""

Seconds = Annotated[int, Field(ge=0, le=24 * 60 * 60)]
"""A duration expressed in whole seconds within a single day."""


class RestrictionKind(StrEnum):
    """封装领域状态与业务约束。"""

    MIN_AGE = "min_age"
    DAILY_LIMIT = "daily_limit"
    SESSION_LIMIT = "session_limit"
    BEDTIME = "bedtime"


# --- Leaf rule nodes --------------------------------------------------------


class AgeGate(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    kind: Literal["age_gate"] = "age_gate"
    min_age: int = Field(ge=0, le=130)


class DailyLimit(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    kind: Literal["daily_limit"] = "daily_limit"
    max_seconds: Seconds


class SessionLimit(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    kind: Literal["session_limit"] = "session_limit"
    max_seconds: Seconds


class BedtimeWindow(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    start: time
    end: time

    @model_validator(mode="after")
    def _reject_empty(self) -> BedtimeWindow:
        if self.start == self.end:
            raise ValueError("bedtime window start and end must differ")
        return self

    def contains(self, local: time) -> bool:
        """执行确定性的业务处理。"""
        if self.start <= self.end:
            return self.start <= local < self.end
        # Wrapping window: inside if after start OR before end.
        return local >= self.start or local < self.end


class BedtimeCurfew(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    kind: Literal["bedtime"] = "bedtime"
    windows: list[BedtimeWindow] = Field(min_length=1)


# --- Exceptions -------------------------------------------------------------


class ApprovedException(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    exception_id: str
    subject_user_id: str
    waives: RestrictionKind
    approved_by: str
    reason: str = ""


# --- Root document ----------------------------------------------------------


class PolicyRules(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    timezone: str = "UTC"
    age_gate: AgeGate | None = None
    daily_limit: DailyLimit | None = None
    session_limit: SessionLimit | None = None
    bedtime: BedtimeCurfew | None = None
    exceptions: list[ApprovedException] = Field(default_factory=list)


class PolicyDocument(BaseModel):
    """封装领域状态与业务约束。"""

    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=200)
    rules: PolicyRules
