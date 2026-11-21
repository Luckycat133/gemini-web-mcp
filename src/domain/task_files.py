"""Verified source identity for a Task-generated file."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TaskFile:
    """A file card bound to a specific completed response candidate."""

    card_id: str
    name: str
    mime_type: str
    download_url: str = field(repr=False)
