"""Common human-readable artifact formatting for MCP adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .mcp_sdk import TextContent

from ..domain import Artifact, DomainResult
from ..domain.artifacts import ArtifactKind, ArtifactState, ArtifactVerificationStatus
from ..domain.operations import OperationReadData


def omit_saved_preview(
    result: DomainResult[OperationReadData],
) -> tuple[DomainResult[OperationReadData], bool]:
    """Omit inline answer text only when a full, verified local report is available."""
    data = result.data
    if not result.ok or data is None or not (data.report or data.answer or data.task_response):
        return result, False
    if not any(artifact.kind is ArtifactKind.REPORT and artifact.state is ArtifactState.LOCAL
               and artifact.verification.status is ArtifactVerificationStatus.VERIFIED and artifact.local_path
               for artifact in data.artifacts):
        return result, False
    warnings = tuple(warning for warning in result.warnings
                     if warning.code not in {"report_preview_truncated", "task_answer_truncated"})
    return replace(result, data=replace(data, report=None, artifact=None, answer=None, task_response=None),
                   warnings=warnings), True


def format_artifact_block(
    artifacts: Sequence[Artifact],
    *,
    heading: str = "Artifacts",
) -> str:
    if not artifacts:
        return ""
    lines = [f"{heading}:"]
    for artifact in artifacts:
        summary = [f"- [{artifact.kind.value}] `{artifact.id}`", f"state={artifact.state.value}"]
        if artifact.title:
            summary.append(artifact.title)
        if artifact.uri:
            summary.append(f"uri={artifact.uri}")
        if artifact.local_path:
            summary.append(f"file={artifact.local_path}")
        if artifact.mime_type:
            summary.append(f"mime={artifact.mime_type}")
        if artifact.size_bytes is not None:
            summary.append(f"bytes={artifact.size_bytes}")
        if artifact.width is not None and artifact.height is not None:
            summary.append(f"dimensions={artifact.width}x{artifact.height}")
        if artifact.duration_seconds is not None:
            summary.append(f"duration={artifact.duration_seconds:.2f}s")
        summary.append(f"verification={artifact.verification.status.value}")
        lines.append(" | ".join(summary))
    return "\n".join(lines)


def append_artifact_block(
    content: list[TextContent],
    artifacts: Sequence[Artifact],
    *,
    heading: str = "Artifacts",
) -> list[TextContent]:
    block = format_artifact_block(artifacts, heading=heading)
    if not content or not block:
        return content
    separator = "\n\n" if content[0].text.strip() else ""
    content[0].text = f"{content[0].text}{separator}{block}"
    return content
