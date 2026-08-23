"""Stable contracts for asynchronous and long-running upstream operations."""

from __future__ import annotations

from dataclasses import dataclass

from .results import OperationState


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
