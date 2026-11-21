"""Bounded, metadata-only view of a single observed Task response."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskActivity:
    label: str | None
    tool_name: str | None
    output_node_count: int
    source_count: int
    depth: int = 0
    status: str = 'unknown'


@dataclass(frozen=True, slots=True)
class TaskProgress:
    chat_id: str
    response_id: str
    candidate_id: str
    activities: tuple[TaskActivity, ...]
    verification: str
    truncated: bool = False
