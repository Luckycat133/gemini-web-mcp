"""Stable conversation lifecycle contracts shared by services and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypeGuard


class SessionLifecycleState(str, Enum):
    """Observable local state for a conversation handle."""

    STATELESS = "stateless"
    ACTIVE = "active"
    REMOVED = "removed"
    EXPIRED = "expired"
    ABSENT = "absent"


class CleanupState(str, Enum):
    """Observable state of the upstream-chat retention decision."""

    NOT_APPLICABLE = "not_applicable"
    RETAINED = "retained"
    PENDING = "pending"
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    FAILED = "failed"
    INVALID_ID = "invalid_id"


@dataclass(frozen=True)
class CleanupObservation:
    """Public-safe evidence for one upstream cleanup decision or attempt.

    ``source`` and ``delete_at`` remain available to local diagnostics, but are
    excluded from agent-facing results. Adapter-specific source labels and
    wall-clock scheduling jitter must not make otherwise identical lifecycle
    metadata look different.
    """

    state: CleanupState = CleanupState.NOT_APPLICABLE
    upstream_chat_id: str | None = None
    attempts: int = 0
    diagnostic_id: str | None = None
    idempotent: bool = False
    source: str = field(default="", metadata={"domain_exclude": True})
    delete_at: float | None = field(
        default=None,
        metadata={"domain_exclude": True},
    )


@dataclass(frozen=True)
class ConversationLifecycleMetadata:
    """Lifecycle metadata embedded in chat and session domain results."""

    session_id: str | None = None
    upstream_chat_id: str | None = None
    session_state: SessionLifecycleState = SessionLifecycleState.STATELESS
    retain_chat: bool = False
    delete_after_seconds: int | None = None
    cleanup: CleanupObservation = field(default_factory=CleanupObservation)

    @classmethod
    def stateless(
        cls,
        *,
        upstream_chat_id: str | None = None,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        cleanup: CleanupObservation | None = None,
    ) -> ConversationLifecycleMetadata:
        return cls(
            upstream_chat_id=upstream_chat_id,
            session_state=SessionLifecycleState.STATELESS,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            cleanup=cleanup or CleanupObservation(),
        )


def cleanup_observation_for_policy(
    upstream_chat_id: str | None,
    *,
    retain_chat: bool,
    source: str = "",
    delete_at: float | None = None,
) -> CleanupObservation:
    """Describe a retention decision without claiming an upstream side effect."""
    if upstream_chat_id is None:
        return CleanupObservation(source=source)
    return CleanupObservation(
        state=CleanupState.RETAINED if retain_chat else CleanupState.PENDING,
        upstream_chat_id=upstream_chat_id,
        source=source,
        delete_at=delete_at,
    )


def is_valid_remote_chat_id(value: object) -> TypeGuard[str]:
    """Return whether a value is a supported Gemini Web chat identifier."""
    return isinstance(value, str) and value.startswith("c_") and len(value) > 2
