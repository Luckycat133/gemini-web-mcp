"""Caller-selected native content references, independent of Web encoding."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class NativeAttachment:
    kind: Literal["drive", "photos", "notebook"]
    resource_id: str
    name: str
    mime_type: str = ""
    url: str | None = None
