"""Unified artifact domain and cross-adapter contract tests."""

import asyncio
import logging
from types import SimpleNamespace

from src.adapters.mcp_sdk import MCPServer

import src.skill_server as skill_server
import src.tools.media as media_tools
from src.adapters import format_artifact_block
from src.domain import (
    ArtifactKind,
    ArtifactResultData,
    ArtifactState,
    ArtifactVerificationStatus,
    DomainErrorCode,
    OperationState,
)
from src.services import (
    artifact_exception_result,
    artifact_from_local_path,
    artifact_from_remote,
    artifact_id,
    artifact_result,
    artifact_save_failure_result,
    classify_artifact_state,
    extract_response_artifacts,
    merge_artifacts,
)


def _domain_payload(content):
    return content[0].meta["domain_result"]


def test_artifact_identity_is_stable_across_remote_and_local_observations(tmp_path):
    uri = "https://cdn.example.test/generated/cat.png"
    path = tmp_path / "cat.png"
    path.write_bytes(b"image-bytes")

    remote = artifact_from_remote(ArtifactKind.IMAGE, uri, title="cat")
    local = artifact_from_local_path(
        ArtifactKind.IMAGE,
        str(path),
        uri=uri,
        title="cat",
        dimensions_probe=lambda _path: (640, 480),
    )

    assert remote.id == local.id == artifact_id(ArtifactKind.IMAGE, uri=uri)
    merged = merge_artifacts((remote,), (local,))
    assert len(merged) == 1
    assert merged[0].state == ArtifactState.LOCAL
    assert merged[0].uri == uri
    assert merged[0].local_path == str(path.resolve())
    assert merged[0].verification.status == ArtifactVerificationStatus.VERIFIED

    failed_local = artifact_from_local_path(
        ArtifactKind.IMAGE,
        str(tmp_path / "missing.png"),
        uri=uri,
    )
    remote_only = merge_artifacts((remote,), (failed_local,))
    assert remote_only[0].state == ArtifactState.REMOTE
    assert remote_only[0].local_path is None

    block = format_artifact_block(merged)
    assert remote.id in block
    assert "state=local" in block
    assert "verification=verified" in block


def test_local_artifact_verifies_file_metadata_dimensions_and_duration(tmp_path):
    image_path = tmp_path / "cover.png"
    image_path.write_bytes(b"not-a-real-image-but-nonempty")
    audio_path = tmp_path / "theme.mp3"
    audio_path.write_bytes(b"audio")

    image = artifact_from_local_path(
        ArtifactKind.IMAGE,
        str(image_path),
        dimensions_probe=lambda _path: (320, 180),
    )
    audio = artifact_from_local_path(
        ArtifactKind.AUDIO,
        str(audio_path),
        duration_probe=lambda _path: 12.5,
    )

    assert image.state == ArtifactState.LOCAL
    assert image.mime_type == "image/png"
    assert image.size_bytes == len(b"not-a-real-image-but-nonempty")
    assert (image.width, image.height) == (320, 180)
    assert "image_dimensions" in image.verification.methods
    assert audio.mime_type == "audio/mpeg"
    assert audio.duration_seconds == 12.5
    assert "duration_probe" in audio.verification.methods


def test_local_artifact_marks_missing_and_empty_files_failed(tmp_path):
    missing = artifact_from_local_path(ArtifactKind.FILE, str(tmp_path / "missing.bin"))
    empty_path = tmp_path / "empty.bin"
    empty_path.touch()
    empty = artifact_from_local_path(ArtifactKind.FILE, str(empty_path))

    assert missing.state == ArtifactState.FAILED
    assert missing.verification.methods == ("file_missing",)
    assert empty.state == ArtifactState.FAILED
    assert empty.size_bytes == 0
    assert "size_empty" in empty.verification.methods


def test_response_extraction_includes_all_remote_modalities_and_backend_evidence():
    response = SimpleNamespace(
        images=[SimpleNamespace(url="https://cdn.test/image.png", title="image", width=50, height=40)],
        videos=[SimpleNamespace(url="https://cdn.test/video.mp4", title="video", duration=8)],
        media=[
            SimpleNamespace(
                mp3_url="https://cdn.test/music.mp3",
                url="https://cdn.test/music.mp4",
                title="music",
            )
        ],
        audio_url="https://cdn.test/direct.mp3",
        metadata=["c_artifact", "r_1"],
        backend="observed-generator",
    )

    artifacts = extract_response_artifacts(
        response,
        media_type="music",
        requested_backend="pro",
        request_model="gemini-3-pro",
        effective_backend="Lyria 3 Pro",
    )

    assert [artifact.kind for artifact in artifacts] == [
        ArtifactKind.IMAGE,
        ArtifactKind.VIDEO,
        ArtifactKind.AUDIO,
        ArtifactKind.VIDEO,
        ArtifactKind.AUDIO,
    ]
    assert all(artifact.state == ArtifactState.REMOTE for artifact in artifacts)
    assert all(artifact.source_chat_id == "c_artifact" for artifact in artifacts)
    assert all(artifact.requested_backend == "pro" for artifact in artifacts)
    assert all(artifact.request_model == "gemini-3-pro" for artifact in artifacts)
    assert all(artifact.effective_backend == "Lyria 3 Pro" for artifact in artifacts)
    assert all(artifact.observed_backend == "observed-generator" for artifact in artifacts)
    assert artifacts[0].width == 50
    assert artifacts[1].duration_seconds == 8.0


def test_artifact_results_distinguish_queued_empty_failed_and_partial():
    queued_data = ArtifactResultData(state=ArtifactState.QUEUED, media_type="video")
    queued = artifact_result(queued_data)
    assert queued.ok is True
    assert queued.meta.operation_state == OperationState.QUEUED

    empty_data = ArtifactResultData(state=ArtifactState.EMPTY, media_type="image")
    empty = artifact_result(empty_data)
    assert empty.error_code == DomainErrorCode.ARTIFACT_NOT_RETURNED.value
    assert empty.retryable is True

    failed_data = ArtifactResultData(state=ArtifactState.FAILED, media_type="music")
    failed = artifact_result(failed_data, save_failures=("audio:verification",))
    assert failed.error_code == DomainErrorCode.ARTIFACT_SAVE_FAILED.value

    remote = artifact_from_remote(ArtifactKind.IMAGE, "https://cdn.test/out.png")
    partial_data = ArtifactResultData(state=ArtifactState.REMOTE, artifacts=(remote,))
    partial = artifact_result(partial_data, save_failures=("image:save",))
    assert partial.ok is True
    assert partial.meta.operation_state == OperationState.PARTIAL
    assert partial.warnings[0].code == "ARTIFACT_SAVE_PARTIAL"


def test_classification_distinguishes_queued_from_empty_response():
    assert classify_artifact_state(SimpleNamespace(status="processing"), ()) == ArtifactState.QUEUED
    assert classify_artifact_state(SimpleNamespace(text=""), ()) == ArtifactState.EMPTY


def test_timeout_failure_keeps_typed_artifact_data():
    data = ArtifactResultData(
        state=ArtifactState.FAILED,
        requested_model="flash",
        request_model="gemini-3-flash",
        effective_backend="Nano Banana 2",
        media_type="image",
    )
    result = artifact_exception_result(
        asyncio.TimeoutError(),
        data,
        logger=logging.getLogger("test.artifact.timeout"),
        operation="test_artifact_timeout",
    )

    assert result.error_code == DomainErrorCode.TIMED_OUT.value
    assert result.meta.operation_state == OperationState.TIMED_OUT
    assert result.data is data
    assert result.to_dict()["data"]["state"] == "failed"


def test_save_exception_has_stable_artifact_error_and_diagnostic():
    data = ArtifactResultData(
        state=ArtifactState.FAILED,
        effective_backend="MCP local renderer",
        observed_backend="filesystem",
        media_type="research_report",
    )
    result = artifact_save_failure_result(
        PermissionError("private path detail"),
        data,
        logger=logging.getLogger("test.artifact.save"),
        operation="test_artifact_save",
    )

    assert result.error_code == DomainErrorCode.ARTIFACT_SAVE_FAILED.value
    assert result.error is not None
    assert result.error.diagnostic_id
    assert "private path detail" not in result.error.message
    assert result.meta.verification_status == "artifact_write_failed"


class _ParityClient:
    def __init__(self, response):
        self.response = response
        self.timeout = 30.0
        self.watchdog_timeout = 120.0

    async def generate_content(self, *args, **kwargs):
        return self.response


async def _no_op_async(*_args, **_kwargs):
    return None


def test_primary_and_compact_media_adapters_return_same_artifact_identity(monkeypatch):
    uri = "https://cdn.example.test/generated/shared.png"
    image = SimpleNamespace(url=uri, title="shared", alt="shared image")
    response = SimpleNamespace(
        text="generated",
        images=[image],
        videos=[],
        media=[],
        metadata=["c_shared", "r_shared"],
        backend="observed-image-backend",
    )

    primary_client = _ParityClient(response)
    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: primary_client)
    monkeypatch.setattr(media_tools, "initialize_client", _no_op_async)
    monkeypatch.setattr(media_tools, "cleanup_due_remote_chats", _no_op_async)
    monkeypatch.setattr(
        media_tools,
        "schedule_remote_chat_cleanup_from_response",
        lambda *_args, **_kwargs: None,
    )
    primary_mcp = MCPServer("artifact-parity-primary")
    media_tools.register_media_tools(primary_mcp)

    async def call_primary():
        result = await primary_mcp.call_tool(
            "gemini_generate_media",
            {"prompt": "shared", "media_type": "image", "model": "flash"},
        )
        return result.content

    primary_content = asyncio.run(call_primary())

    compact_client = _ParityClient(response)
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: compact_client)
    monkeypatch.setattr(skill_server, "initialize_client", _no_op_async)
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", _no_op_async)
    monkeypatch.setattr(
        skill_server,
        "validate_optional_image_path",
        lambda _path: (True, None, None),
    )
    monkeypatch.setattr(skill_server, "_schedule_skill_response_cleanup", lambda *_args: None)
    compact_content = asyncio.run(skill_server.create(prompt="shared", type="image", model="flash"))

    primary_data = _domain_payload(primary_content)["data"]
    compact_data = _domain_payload(compact_content)["data"]
    assert primary_data["artifacts"][0]["id"] == compact_data["artifacts"][0]["id"]
    assert primary_data["artifacts"][0]["uri"] == uri
    assert compact_data["artifacts"][0]["uri"] == uri
    assert primary_data["observed_backend"] == "observed-image-backend"
    assert compact_data["observed_backend"] == "observed-image-backend"
    assert primary_data["effective_backend"] == compact_data["effective_backend"] == "Nano Banana 2"
