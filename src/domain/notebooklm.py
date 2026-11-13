"""Frozen domain contracts for built-in Gemini NotebookLM notebooks.

Notebook source references and Gemini project entries have separate transport
shapes. This module owns typed picker identities, lists and selections.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NotebookLMNotebook:
    """One verified built-in NotebookLM notebook identity.

    IDs are opaque nonblank strings and are not assumed to be UUIDs.
    ``source_count`` and ``created_at`` are optional because malformed
    upstream data cannot be verified: unknown counts and times are
    preserved as ``None`` instead of being invented or dropped.
    """

    notebook_id: str
    title: str
    source_count: int | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class NotebookLMListData:
    """Compact typed page returned by ``NotebookLMService.list_notebooks``."""

    source: str = "notebooklm"
    items: tuple[NotebookLMNotebook, ...] = field(default_factory=tuple)
    count: int = 0
    total_count: int = 0
    has_more: bool = False
    next_offset: int | None = None
    query: str = ""


@dataclass(frozen=True)
class NotebookLMSelectionData:
    """Typed exact selection returned by ``NotebookLMService.resolve_notebooks``."""

    items: tuple[NotebookLMNotebook, ...] = field(default_factory=tuple)
    count: int = 0
