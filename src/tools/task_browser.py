"""Thin MCP adapter for bounded Task browser observation and input."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from ..adapters import append_artifact_block, domain_failure_text, domain_text
from ..adapters.mcp_sdk import ImageContent, MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client
from ..domain import DomainErrorCode, DomainResult
from ..domain.artifacts import Artifact, ArtifactKind, ArtifactState, ArtifactVerificationStatus
from ..services.task_browser import TaskBrowserDependencies, TaskBrowserService
from ..services.web_context_transport import fetch_task_metadata, fetch_task_browser_turn
from .annotations import DESTRUCTIVE_REMOTE

_MAX_INLINE_IMAGE_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _service() -> TaskBrowserService:
    return TaskBrowserService(TaskBrowserDependencies(
        client_initializer=initialize_client,
        client_provider=get_gemini_client,
        read_turn=fetch_task_browser_turn,
        read_task=fetch_task_metadata,
    ))


def _inline_image(artifact: Artifact) -> ImageContent | None:
    if (artifact.kind is not ArtifactKind.IMAGE or artifact.state is not ArtifactState.LOCAL
            or artifact.verification.status is not ArtifactVerificationStatus.VERIFIED
            or artifact.mime_type != 'image/png' or not artifact.local_path):
        return None
    expected = artifact.verification.details.get('sha256')
    if not isinstance(expected, str):
        return None
    try:
        with Path(artifact.local_path).open('rb') as stream:
            data = stream.read(_MAX_INLINE_IMAGE_BYTES + 1)
    except (OSError, ValueError):
        return None
    if (len(data) > _MAX_INLINE_IMAGE_BYTES or not data.startswith(_PNG_MAGIC)
            or hashlib.sha256(data).hexdigest() != expected):
        return None
    return ImageContent(type='image', data=base64.b64encode(data).decode('ascii'), mimeType='image/png')  # type: ignore[call-arg]


def register_task_browser_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    async def gemini_task_browser(
        chat_id: str,
        response_id: str | None = None,
        candidate_id: str | None = None,
        browser_input: dict[str, Any] | None = None,
        action_id: str | None = None,
        expected_frame_sha256: str | None = None,
        known_frame_sha256: str | None = None,
        include_image: bool = True,
    ) -> list[TextContent | ImageContent]:
        """Capture a verified Task browser frame or send one explicitly requested input.

        Read the latest frame or provide exact response_id and candidate_id.
        For observation only, pass the last frame hash as known_frame_sha256 to
        receive metadata when unchanged. Set include_image=false for file only.
        Input requires both IDs, expected_frame_sha256, a unique action_id (20+
        characters), and a completed or waiting Task. Use click/scroll/key/type
        and frame x/y. Actions never replay. Verify website effects independently;
        exclusive pause is unavailable. Requires the browser-control extra.
        """
        try:
            result = await _service().observe(
                chat_id, response_id=response_id, candidate_id=candidate_id,
                browser_input=browser_input, action_id=action_id,
                expected_frame_sha256=expected_frame_sha256,
                known_frame_sha256=known_frame_sha256,
            )
        except Exception:
            result = DomainResult.failure(
                DomainErrorCode.INTERNAL_ERROR, 'Task browser request could not be completed.',
                verification_status='browser_adapter_unverified',
            )
        artifacts = result.data.artifacts if result.data is not None else ()
        text = 'Task browser observation returned.' if result.ok else domain_failure_text(result)
        if result.data is not None and result.data.state == 'frame_unchanged':
            text = 'Task browser frame unchanged.'
        content: list[TextContent | ImageContent] = list(append_artifact_block(
            domain_text(result, text, use_result_data=True), artifacts,
        ))
        if include_image:
            for artifact in artifacts:
                image = _inline_image(artifact)
                if image is not None:
                    content.append(image)
        return content
