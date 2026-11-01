"""Stable contracts for asynchronous and long-running upstream operations."""

from __future__ import annotations

from dataclasses import dataclass

from .conversations import CleanupObservation
from .artifacts import Artifact, ArtifactState
from .results import DomainErrorCode, OperationState


def operation_failure_code(code: str | None) -> DomainErrorCode:
    if code == "UPSTREAM_LIMIT_EXCEEDED":
        return DomainErrorCode.RATE_LIMITED
    try:
        return DomainErrorCode(code) if code else DomainErrorCode.UPSTREAM_REJECTED
    except ValueError:
        return DomainErrorCode.INTERNAL_ERROR


@dataclass(frozen=True)
class LongOperationData:
    """Client-visible state and continuation handles for one long operation."""

    operation: str
    state: OperationState
    operation_id: str | None = None
    upstream_operation_id: str | None = None
    upstream_chat_id: str | None = None
    title: str | None = None
    latest_upstream_state: str | None = None
    continuation_possible: bool = False
    report_available: bool = False
    poll_count: int = 0
    cleanup: CleanupObservation | None = None
    media_type: str | None = None
    artifacts_available: bool = False
    poll_after_seconds: int = 5
    error_code: str | None = None
    execution_mode: str | None = None


@dataclass(frozen=True)
class InlineReportArtifact:
    """Metadata for a verified report returned inline in the same result."""

    id: str
    kind: str
    mime_type: str
    size_bytes: int
    sha256: str
    delivery: str = "inline"
    verification: str = "research_document_readback"


@dataclass(frozen=True)
class OperationReadData:
    """A single explicit operation read; report text is never persisted in SQLite."""

    operation_id: str
    operation: str
    state: OperationState
    continuation_possible: bool
    report_available: bool
    verification_status: str
    report: str | None = None
    artifact: InlineReportArtifact | None = None
    read_window_limit: int = 0
    cleanup: CleanupObservation | None = None
    media_type: str | None = None
    artifacts: tuple[Artifact, ...] = ()
    artifact_state: ArtifactState | None = None
    worker_active: bool = False
    error_code: str | None = None
    task_response: str | None = None
    upstream_chat_id: str | None = None
    upstream_status: int | None = None
    answer: str | None = None
    execution_mode: str | None = None
    input_required: bool = False
    input_prompt: str | None = None
    input_url: str | None = None
    input_id: str | None = None
    # Public labels/prompts and opaque card-bound input_option_id only; native
    # choice, action and subagent identities remain transient transport data.
    input_options: tuple[dict[str, str], ...] = ()
    # Native app connection is separate from an executable Task confirmation.
    # Numeric goal statuses alone never establish this classification.
    needs_attention: bool = False
    setup_required: bool = False
    setup_kind: str | None = None
    setup_app_name: str | None = None
    setup_url: str | None = None
    upstream_operation_id: str | None = None
