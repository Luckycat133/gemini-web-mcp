"""Typed multimodal artifact contracts shared across tool surfaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ArtifactKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    FILE = "file"
    REPORT = "report"
    WEBPAGE = "webpage"
    DATA = "data"


class ArtifactState(str, Enum):
    """Observable availability of an artifact or artifact-producing request."""

    REMOTE = "remote"
    LOCAL = "local"
    QUEUED = "queued"
    EMPTY = "empty"
    FAILED = "failed"


class ArtifactVerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    FAILED = "failed"


@dataclass(frozen=True)
class ArtifactVerification:
    status: ArtifactVerificationStatus
    methods: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Artifact:
    """One stable artifact identity with independent remote and local locations."""

    id: str
    kind: ArtifactKind
    state: ArtifactState
    title: str | None = None
    uri: str | None = None
    local_path: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    source_chat_id: str | None = None
    requested_backend: str | None = None
    request_model: str | None = None
    effective_backend: str | None = None
    observed_backend: str | None = None
    verification: ArtifactVerification = field(
        default_factory=lambda: ArtifactVerification(
            ArtifactVerificationStatus.UNVERIFIED,
        )
    )


@dataclass(frozen=True)
class ArtifactResultData:
    """Public result data for media, file, URL, and report workflows."""

    state: ArtifactState
    artifacts: tuple[Artifact, ...] = ()
    input_artifacts: tuple[Artifact, ...] = ()
    requested_model: str | None = None
    request_model: str | None = None
    effective_backend: str | None = None
    observed_backend: str | None = None
    source_chat_id: str | None = None
    media_type: str | None = None
