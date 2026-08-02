"""Typed domain contracts shared by services and MCP adapters."""

from .artifacts import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    ArtifactVerification,
    ArtifactVerificationStatus,
)
from .results import (
    DomainError,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    ResultMeta,
    new_diagnostic_id,
    new_request_id,
    result_from_exception,
)

__all__ = [
    "Artifact",
    "ArtifactKind",
    "ArtifactResultData",
    "ArtifactState",
    "ArtifactVerification",
    "ArtifactVerificationStatus",
    "DomainError",
    "DomainErrorCode",
    "DomainResult",
    "DomainWarning",
    "OperationState",
    "ResultMeta",
    "new_diagnostic_id",
    "new_request_id",
    "result_from_exception",
]
