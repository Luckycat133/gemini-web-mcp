"""Transient, metadata-only identities for native Gemini permission preferences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PermissionRecord:
    """An observed permission, with native identity fields retained losslessly.

    Native values are transient transport metadata, not a public response or
    authorization. ``source='catalog'`` may describe a manageable action with
    no stored grant; a missing scope stays unknown (0), never user-wide (2).
    """

    native_permission_id: list[Any] = field(repr=False)
    grant_id: str
    scope: int
    context: list[Any] | None = field(repr=False)
    state: int
    label: str
    category: int | None
    native_grant_identity: list[Any] = field(default_factory=list, repr=False)
    source: str = "stored"

    @property
    def tool_name(self) -> str | None:
        handle = self.native_permission_id[0] if self.native_permission_id else None
        if isinstance(handle, list) and handle and isinstance(handle[0], str) and handle[0].strip():
            return handle[0]
        return None

    @property
    def function_name(self) -> str | None:
        handle = self.native_permission_id[0] if self.native_permission_id else None
        if isinstance(handle, list) and len(handle) > 1 and isinstance(handle[1], str) and handle[1].strip():
            return handle[1]
        return None

    @property
    def attributes(self) -> tuple[tuple[str, str], ...]:
        """Known key/value projections; the full native pairs remain untouched."""
        entries = self.native_permission_id[9] if len(self.native_permission_id) > 9 else None
        if not isinstance(entries, list):
            return ()
        return tuple((entry[0], entry[1]) for entry in entries
                     if isinstance(entry, list) and len(entry) >= 2
                     and isinstance(entry[0], str) and isinstance(entry[1], str))
