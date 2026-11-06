"""Typed contracts for Gemini Web Task (Remy goal) metadata.

Gemini Web exposes Tasks (Remy goals) as goal metadata attached to a chat: a
description, an upstream status, pin state, and timestamps. Conversation text
is never part of a task. This module declares only the domain boundary: the
observed task metadata, page and mutation outcome types. It invents no RPC
ids, payload indices, endpoint URLs, browser selectors, or SDK methods.

Task status meaning is intentionally partial and evidence-backed: upstream
status 3 is completed, 4 is could-not-complete (failed), and 5 is needs-input
(input_required). Every other status (including None and bools) is unknown,
and the upstream integer is preserved verbatim. Goal metadata does not contain the separately modeled exact-turn activity tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

# Verified upstream Task (Remy goal) statuses matched by the Gemini legacy
# frontend to task states. Every other status (including None and bools) is
# unknown; the upstream integer is preserved verbatim and never coerced.
TASK_STATUS_COMPLETED = 3
TASK_STATUS_FAILED = 4          # could-not-complete
TASK_STATUS_INPUT_REQUIRED = 5  # needs-input

# Single shared pure mapping from upstream goal status to task state, used by
# every task dataclass so one status means one state across the domain.
_GOAL_STATUS_TO_STATE: dict[int, TaskState] = {
    TASK_STATUS_COMPLETED: "completed",
    TASK_STATUS_FAILED: "failed",
    TASK_STATUS_INPUT_REQUIRED: "input_required",
}


def goal_state_from_upstream_status(upstream_status: int | None) -> TaskState:
    """Return the task state for an observed upstream goal status.

    Only verified numeric statuses are mapped. Bools are not ints and are
    unknown; None and unrecognized integers are unknown too. The upstream
    value itself is never coerced or mutated by this function.
    """
    if type(upstream_status) is not int:
        return "unknown"
    return _GOAL_STATUS_TO_STATE.get(upstream_status, "unknown")

# Service-side limits. Gemini Web publishes no public task pagination or
# length contract, so these are local guards, not observed upstream constants.
DEFAULT_TASK_LIST_LIMIT = 20
MAX_TASK_DESCRIPTION_PREVIEW = 160
DEFAULT_TASK_GET_CHARS = 800
MAX_TASK_GET_CHARS = 4000
MAX_TASK_TITLE_LENGTH = 200

_TRUNCATION_INDICATOR = "…"  # "…"; keeps every preview within its limit

TaskState = Literal["completed", "failed", "input_required", "unknown"]
TaskMetadataAction = Literal["rename", "pin", "unpin"]
TaskMetadataField = Literal["description", "pinned"]


def truncate_text(text: str, limit: int) -> tuple[str, bool]:
    """Return ``text`` truncated to at most ``limit`` characters.

    When truncated, the truncation indicator is appended and the result never
    exceeds ``limit`` characters. The flag reports whether truncation
    happened.
    """
    if len(text) <= limit:
        return text, False
    keep = max(0, limit - len(_TRUNCATION_INDICATOR))
    return text[:keep] + _TRUNCATION_INDICATOR, True


# Verification statuses consumed by src/services/web_tasks.py.
STATUS_VERIFIED = "verified"
STATUS_NO_OP = "no_op"
STATUS_ACCOUNT_REQUIRED = "account_required"
STATUS_ACCOUNT_MISMATCH = "account_mismatch"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_ABSENT = "absent"
STATUS_WRONG_IDENTITY = "wrong_identity"
STATUS_READ_ERROR = "read_error"
STATUS_UNVERIFIED = "unverified"


@dataclass(frozen=True)
class WebTask:
    """Freshly observed Task (Remy goal) metadata; conversation text is never included."""

    chat_id: str
    description: str | None
    upstream_status: int | None
    is_pinned: bool | None
    created_at: str | int | float | None
    updated_at: str | int | float | None
    state: TaskState = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state",
            goal_state_from_upstream_status(self.upstream_status),
        )


@dataclass(frozen=True)
class WebTaskSummary:
    """One list page item with a description preview bounded to 160 characters."""

    chat_id: str
    description_preview: str
    description_truncated: bool
    upstream_status: int | None
    is_pinned: bool | None
    created_at: str | int | float | None
    updated_at: str | int | float | None
    state: TaskState = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state",
            goal_state_from_upstream_status(self.upstream_status),
        )


@dataclass(frozen=True)
class WebTaskPage(Generic[T]):
    """One page of items; item type depends on the caller (raw tasks or summaries)."""

    items: tuple[T, ...]
    next_page_token: str | None


@dataclass(frozen=True)
class WebTaskGetData:
    """Freshly observed task for one chat, with description bounded by max_chars."""

    chat_id: str
    description: str | None
    description_truncated: bool
    upstream_status: int | None
    is_pinned: bool | None
    created_at: str | int | float | None
    updated_at: str | int | float | None
    state: TaskState = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "state",
            goal_state_from_upstream_status(self.upstream_status),
        )


@dataclass(frozen=True)
class WebTaskMetadataUpdateData:
    """The requested field is exposed only after independent exact Task read-back."""

    action: TaskMetadataAction
    chat_id: str
    changed: bool | None = False
    mutation_sent: bool | None = False
    verified: bool = False
    description: str | None = None
    is_pinned: bool | None = None


@dataclass(frozen=True)
class WebTaskDeleteData:
    """Outcome of one task deletion request with read-back evidence.

    ``changed`` is evidence-based: False before a mutation is sent, None once
    a mutation was sent but its effect is not independently proven, and True
    only when a fresh read-back proves the task is absent. ``mutation_sent``
    records whether the deletion call was dispatched regardless of the
    verification outcome.
    """

    chat_id: str
    changed: bool | None = False
    mutation_sent: bool = False
    verified: bool = False
    pre_read_task: WebTask | None = None
    post_read_task: WebTask | None = None


__all__ = [
    "DEFAULT_TASK_GET_CHARS",
    "DEFAULT_TASK_LIST_LIMIT",
    "MAX_TASK_DESCRIPTION_PREVIEW",
    "MAX_TASK_GET_CHARS",
    "MAX_TASK_TITLE_LENGTH",
    "STATUS_ABSENT",
    "STATUS_ACCOUNT_MISMATCH",
    "STATUS_ACCOUNT_REQUIRED",
    "STATUS_INVALID_REQUEST",
    "STATUS_NO_OP",
    "STATUS_READ_ERROR",
    "STATUS_UNVERIFIED",
    "STATUS_VERIFIED",
    "STATUS_WRONG_IDENTITY",
    "TASK_STATUS_COMPLETED",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_INPUT_REQUIRED",
    "TaskMetadataAction",
    "TaskMetadataField",
    "TaskState",
    "truncate_text",
    "WebTask",
    "WebTaskDeleteData",
    "WebTaskGetData",
    "WebTaskMetadataUpdateData",
    "WebTaskPage",
    "WebTaskSummary",
    "goal_state_from_upstream_status",
]
