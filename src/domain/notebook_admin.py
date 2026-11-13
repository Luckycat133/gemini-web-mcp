"""Native Notebook administration result contracts."""
from dataclasses import dataclass


@dataclass(frozen=True)
class NotebookChange:
    notebook_id: str | None
    source_id: str | None
    verified: bool
    changed: bool | None
    content_verified: bool = False


@dataclass(frozen=True)
class NotebookRegisteredSourceChange(NotebookChange):
    source_state: int | None = None


@dataclass(frozen=True)
class NotebookURLSourceChange(NotebookRegisteredSourceChange):
    url_verified: bool = False


@dataclass(frozen=True)
class NotebookFileSourceChange(NotebookRegisteredSourceChange):
    upload_reference_verified: bool = False
