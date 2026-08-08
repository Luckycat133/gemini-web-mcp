"""Common human-readable artifact formatting for MCP adapters."""

from __future__ import annotations

from collections.abc import Sequence

from .mcp_sdk import TextContent

from ..domain import Artifact


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
