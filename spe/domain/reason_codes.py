"""服务端业务模块。"""

from __future__ import annotations

from enum import StrEnum


class ReasonCode(StrEnum):
    """封装领域状态与业务约束。"""

    # --- Allow outcomes -----------------------------------------------------
    ALLOWED = "ALLOWED"
    """The action is permitted; no restriction applied."""

    ALLOWED_BY_EXCEPTION = "ALLOWED_BY_EXCEPTION"
    """An approved exception overrode a restriction that would otherwise deny."""

    # --- Deny outcomes: eligibility ----------------------------------------
    DENIED_UNDER_MIN_AGE = "DENIED_UNDER_MIN_AGE"
    """The user's age is below the policy's minimum age requirement."""

    # --- Deny outcomes: quota ----------------------------------------------
    DENIED_DAILY_LIMIT_REACHED = "DENIED_DAILY_LIMIT_REACHED"
    """The user has consumed their per-day watch-time budget."""

    DENIED_SESSION_LIMIT_REACHED = "DENIED_SESSION_LIMIT_REACHED"
    """The current session has reached its maximum allowed duration."""

    # --- Deny outcomes: time windows ---------------------------------------
    DENIED_BEDTIME_CURFEW = "DENIED_BEDTIME_CURFEW"
    """The local time falls inside a configured bedtime curfew window."""

    # --- Lifecycle / request-level reasons ---------------------------------
    SESSION_STARTED = "SESSION_STARTED"
    """A new session was created and pinned to the active policy version."""

    SESSION_STARTED_IDEMPOTENT = "SESSION_STARTED_IDEMPOTENT"
    """An existing session was returned for a repeated idempotency key."""

    HEARTBEAT_APPLIED = "HEARTBEAT_APPLIED"
    """A heartbeat advanced usage accounting for an active session."""

    HEARTBEAT_IGNORED_STALE = "HEARTBEAT_IGNORED_STALE"
    """A duplicate or out-of-order heartbeat was ignored (no double counting)."""

    SESSION_PAUSED = "SESSION_PAUSED"
    """The session transitioned to the paused state."""

    SESSION_RESUMED = "SESSION_RESUMED"
    """The session transitioned from paused back to active."""

    SESSION_ENDED = "SESSION_ENDED"
    """The session was terminated and finalised; usage is now immutable."""

    SESSION_ENDED_BY_LIMIT = "SESSION_ENDED_BY_LIMIT"
    """The session was auto-ended because a hard limit was hit during heartbeat."""

    # --- Rejections (invalid requests / conflicts) -------------------------
    REJECTED_ACTIVE_SESSION_EXISTS = "REJECTED_ACTIVE_SESSION_EXISTS"
    """A concurrent start lost the race; the user already has an active session."""

    REJECTED_SESSION_NOT_FOUND = "REJECTED_SESSION_NOT_FOUND"
    """The referenced session does not exist within the caller's tenant."""

    REJECTED_SESSION_NOT_ACTIVE = "REJECTED_SESSION_NOT_ACTIVE"
    """The action requires an active session but the session is paused/ended."""

    REJECTED_SESSION_NOT_PAUSED = "REJECTED_SESSION_NOT_PAUSED"
    """Resume was requested on a session that is not currently paused."""

    REJECTED_SESSION_ENDED = "REJECTED_SESSION_ENDED"
    """The action is not allowed because the session has already ended."""

    REJECTED_POLICY_NOT_FOUND = "REJECTED_POLICY_NOT_FOUND"
    """No published policy exists for the tenant (or requested version)."""

    REJECTED_POLICY_INVALID = "REJECTED_POLICY_INVALID"
    """The submitted policy document failed static validation."""

    REJECTED_TENANT_MISMATCH = "REJECTED_TENANT_MISMATCH"
    """The resource belongs to a different tenant than the caller."""
