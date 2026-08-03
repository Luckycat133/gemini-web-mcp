"""Typed domain contracts shared by services and MCP adapters."""

from .artifacts import (
    Artifact,
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    ArtifactVerification,
    ArtifactVerificationStatus,
)
from .conversations import (
    CleanupObservation,
    CleanupState,
    ConversationLifecycleMetadata,
    SessionLifecycleState,
    StreamChunkSemantics,
    StreamCollectionMetadata,
    StreamDelivery,
    cleanup_observation_for_policy,
    is_valid_remote_chat_id,
)
from .operations import LongOperationData
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
    "CleanupObservation",
    "CleanupState",
    "ConversationLifecycleMetadata",
    "DomainError",
    "DomainErrorCode",
    "DomainResult",
    "DomainWarning",
    "LongOperationData",
    "OperationState",
    "ResultMeta",
    "SessionLifecycleState",
    "StreamChunkSemantics",
    "StreamCollectionMetadata",
    "StreamDelivery",
    "cleanup_observation_for_policy",
    "is_valid_remote_chat_id",
    "new_diagnostic_id",
    "new_request_id",
    "result_from_exception",
]
