"""Native Notebook administration result contracts."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NotebookChange:
    notebook_id: str | None
    source_id: str | None
    verified: bool
    changed: bool | None
    content_verified: bool = False
