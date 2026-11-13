"""Unified artifact domain and cross-adapter contract tests."""

import asyncio
import base64
import logging
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from gemini_webapi.types import Candidate, ModelOutput
from gemini_webapi.types.image import GeneratedImage, WebImage
from gemini_webapi.types.video import GeneratedVideo

from src.adapters.mcp_sdk import MCPServer

import src.skill_server as skill_server
import src.tools.media as media_tools
import src.services.media_storage as media_storage
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


def test_local_image_mime_uses_signature_when_suffix_is_wrong(tmp_path):
    image_path = tmp_path / "actually-jpeg.png"
    image_path.write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg-content")

    image = artifact_from_local_path(ArtifactKind.IMAGE, str(image_path))

    assert image.mime_type == "image/jpeg"
    assert "image_mime_signature" in image.verification.methods


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


def test_response_extraction_limits_music_artifacts_and_preserves_backend_evidence():
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


def test_media_requests_do_not_count_other_media_types_as_artifacts():
    response = SimpleNamespace(
        images=[SimpleNamespace(url="https://example.test/cover.png")],
        videos=[SimpleNamespace(url="https://example.test/unrelated.mp4")],
        media=[], metadata=[],
    )
    assert not extract_response_artifacts(response, media_type="music")
    assert [a.kind for a in extract_response_artifacts(response, media_type="video")] == [ArtifactKind.VIDEO]
    assert [a.kind for a in extract_response_artifacts(response, media_type="image")] == [ArtifactKind.IMAGE]


def test_jpeg_bytes_cannot_be_verified_as_audio_or_video(tmp_path):
    preview = tmp_path / "fake-video.mp4"
    preview.write_bytes(b"\xff\xd8\xff\xe0" + b"jpeg preview")
    for kind in (ArtifactKind.AUDIO, ArtifactKind.VIDEO):
        artifact = artifact_from_local_path(kind, str(preview))
        assert artifact.state == ArtifactState.FAILED
        assert artifact.mime_type == "image/jpeg"
        assert "media_type_mismatch" in artifact.verification.methods


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

    def video_generation_scope(self):
        return nullcontext()

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
        "finalize_remote_chat_cleanup_from_response",
        _no_op_async,
    )
    primary_mcp = MCPServer("artifact-parity-primary")
    media_tools.register_media_tools(primary_mcp)

    async def call_primary():
        result = await primary_mcp.call_tool(
            "gemini_generate_media",
            {"background": False, "prompt": "shared", "media_type": "image", "model": "flash"},
        )
        return result.content

    primary_content = asyncio.run(call_primary())

    compact_client = _ParityClient(response)
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: compact_client)
    monkeypatch.setattr(skill_server, "initialize_client", _no_op_async)
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", _no_op_async)
    monkeypatch.setattr(skill_server, "finalize_remote_chat_cleanup_from_response", _no_op_async)
    monkeypatch.setattr(
        skill_server,
        "validate_optional_image_path",
        lambda _path: (True, None, None),
    )
    monkeypatch.setattr(skill_server, "_schedule_skill_response_cleanup", lambda *_args: None)
    compact_content = asyncio.run(skill_server.create(background=False, prompt="shared", type="image", model="flash"))

    primary_data = _domain_payload(primary_content)["data"]
    compact_data = _domain_payload(compact_content)["data"]
    assert primary_data["artifacts"][0]["id"] == compact_data["artifacts"][0]["id"]
    assert primary_data["artifacts"][0]["uri"] == uri
    assert compact_data["artifacts"][0]["uri"] == uri
    assert primary_data["observed_backend"] == "observed-image-backend"
    assert compact_data["observed_backend"] == "observed-image-backend"
    assert primary_data["effective_backend"] == compact_data["effective_backend"] == "Nano Banana 2"


def test_generated_image_extraction_uses_selected_candidate_provenance():
    web = WebImage(url="https://example.test/search.png")
    generated = GeneratedImage(url="https://example.test/generated.png")
    response = ModelOutput(
        metadata=[],
        candidates=[
            Candidate(rcid="one", text="first", web_images=[web], generated_images=[generated]),
            Candidate(rcid="two", text="second", web_images=[web]),
        ],
        chosen=1,
    )
    assert not extract_response_artifacts(response, media_type="image")
    assert extract_response_artifacts(response)[0].uri == web.url
    response.chosen = 0
    assert [item.uri for item in extract_response_artifacts(response, media_type="image")] == [generated.url]


def test_web_images_do_not_prove_generated_image_on_either_surface(monkeypatch):
    web = WebImage(url="https://example.test/search.png")
    response = ModelOutput(
        metadata=[],
        candidates=[Candidate(rcid="one", text="I found an image", web_images=[web])],
    )
    client = _ParityClient(response)
    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: client)
    monkeypatch.setattr(media_tools, "initialize_client", _no_op_async)
    monkeypatch.setattr(media_tools, "cleanup_due_remote_chats", _no_op_async)
    monkeypatch.setattr(media_tools, "finalize_remote_chat_cleanup_from_response", _no_op_async)
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: client)
    monkeypatch.setattr(skill_server, "initialize_client", _no_op_async)
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", _no_op_async)
    monkeypatch.setattr(skill_server, "finalize_remote_chat_cleanup_from_response", _no_op_async)
    mcp = MCPServer("web-image-parity")
    media_tools.register_media_tools(mcp)

    async def call_primary():
        return (await mcp.call_tool("gemini_generate_media", {"background": False, "prompt": "mug", "media_type": "image"})).content

    primary = asyncio.run(call_primary())
    compact = asyncio.run(skill_server.create(background=False, prompt="mug", type="image"))
    for content in (primary, compact):
        payload = _domain_payload(content)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "ARTIFACT_NOT_RETURNED"
        assert not payload["data"]["artifacts"]
        assert web.url not in content[0].text
    assert web.url in skill_server._format_response(response)[0].text


def test_compact_music_recovers_the_observed_turn(monkeypatch):
    response = SimpleNamespace(text="pending", images=[], videos=[], media=[], metadata=["c_1", "r_1", "rc_1"])
    client = _ParityClient(response)
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: client)
    monkeypatch.setattr(skill_server, "initialize_client", _no_op_async)
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", _no_op_async)
    monkeypatch.setattr(skill_server, "finalize_remote_chat_cleanup_from_response", _no_op_async)
    monkeypatch.setattr(skill_server, "_schedule_skill_response_cleanup", lambda *_a, **_kw: None)

    async def recover(got_client, cid, *, rid, rcid):
        assert (got_client, cid, rid, rcid) == (client, "c_1", "r_1", "rc_1")
        return [SimpleNamespace(mp3_url="https://example.test/song.mp3", url="", title="song")]

    monkeypatch.setattr(skill_server, "fetch_music_media_from_chat", recover)
    content = asyncio.run(skill_server.create(background=False, prompt="piano", type="music"))
    payload = _domain_payload(content)
    assert payload["ok"] is True
    assert payload["data"]["artifacts"][0]["uri"] == "https://example.test/song.mp3"


def test_primary_and_compact_music_share_verified_local_saving(monkeypatch, tmp_path):
    events = []
    payload_bytes = b"\xff\xfb\x90\x64" + b"\0" * 20

    class Music:
        mp3_url = "https://example.test/song.mp3"
        url = ""
        title = "song"

        async def save(self, *, path, filename, verbose, download_type):
            assert download_type == "audio"
            events.append("save")
            output = Path(path) / filename
            output.write_bytes(payload_bytes)
            return {"audio": str(output)}

    response = SimpleNamespace(text="done", images=[], videos=[], media=[Music()], metadata=["c_shared", "r_shared"])
    client = _ParityClient(response)

    async def finalize(*_args, **_kwargs):
        events.append("cleanup")

    for module in (media_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", _no_op_async)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", _no_op_async)
        monkeypatch.setattr(module, "finalize_remote_chat_cleanup_from_response", finalize)
    monkeypatch.setattr(media_tools, "probe_playable_av_tracks", lambda path: frozenset({"audio"}))
    monkeypatch.setattr(media_storage, "probe_playable_av_tracks", lambda path: frozenset({"audio"}))
    mcp = MCPServer("music-local-parity")
    media_tools.register_media_tools(mcp)

    async def run():
        primary = (await mcp.call_tool("gemini_generate_media", {"background": False,
            "prompt": "piano", "media_type": "music", "output_dir": str(tmp_path / "primary"),
            "filename": "song.mp3",
        })).content
        compact = await skill_server.create(background=False, prompt="piano", type="music", output_dir=str(tmp_path / "compact"),
                                            filename="song.mp3")
        return primary, compact

    primary, compact = asyncio.run(run())
    assert events == ["save", "cleanup", "save", "cleanup"]
    for content in (primary, compact):
        result = _domain_payload(content)
        artifact = result["data"]["artifacts"][0]
        assert result["ok"] and artifact["state"] == "local"
        assert artifact["mime_type"] == "audio/mpeg"
        assert Path(artifact["local_path"]).read_bytes() == payload_bytes


def test_thumbnail_only_video_fails_on_both_surfaces(monkeypatch, tmp_path):
    jpeg = b"\xff\xd8\xff\xe0" + b"jpeg preview"

    class Video:
        url = "https://example.test/video.mp4"
        thumbnail = "https://example.test/preview.jpg"
        title = "video"

        async def save(self, *, path, filename, verbose):
            destination = Path(path)
            destination.mkdir(parents=True, exist_ok=True)
            thumb = destination / "preview.jpg"
            thumb.write_bytes(jpeg)
            return {"video": None, "video_thumbnail": str(thumb)}

    response = SimpleNamespace(text="done", images=[], videos=[Video()], media=[], metadata=["c_shared", "r_1"])
    client = _ParityClient(response)
    for module in (media_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", _no_op_async)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", _no_op_async)
        monkeypatch.setattr(module, "finalize_remote_chat_cleanup_from_response", _no_op_async)
        monkeypatch.setattr(module, "schedule_remote_chat_cleanup_from_response", lambda *_a, **_kw: None)
    mcp = MCPServer("video-preview-parity")
    media_tools.register_media_tools(mcp)

    async def run():
        primary = (await mcp.call_tool("gemini_generate_media", {"background": False,
            "prompt": "ball", "media_type": "video", "output_dir": str(tmp_path / "primary"),
            "filename": "clip.mp4",
        })).content
        compact = await skill_server.create(background=False, prompt="ball", type="video", output_dir=str(tmp_path / "compact"),
                                            filename="clip.mp4")
        return primary, compact

    for content in asyncio.run(run()):
        result = _domain_payload(content)
        assert not result["ok"]
        assert result["error"]["code"] == "ARTIFACT_SAVE_FAILED"
        assert result["data"]["state"] == "failed"
        assert any(item["kind"] == "image" and item["mime_type"] == "image/jpeg" and item["state"] == "local"
                   for item in result["data"]["artifacts"])
        assert not any(item["kind"] == "video" and item["state"] == "local" for item in result["data"]["artifacts"])


def test_video_and_thumbnail_stay_distinct_on_both_surfaces(monkeypatch, tmp_path):
    mp4 = b"\x00\x00\x00\x18ftypisom" + b"\0" * 12
    jpeg = b"\xff\xd8\xff\xe0" + b"jpeg preview"

    class Video:
        url = "https://example.test/video.mp4"
        thumbnail = "https://example.test/preview.jpg"
        title = "video"

        async def save(self, *, path, filename, verbose):
            destination = Path(path)
            destination.mkdir(parents=True, exist_ok=True)
            video = destination / "clip.mp4"
            thumb = destination / "preview.jpg"
            video.write_bytes(mp4)
            thumb.write_bytes(jpeg)
            return {"video": str(video), "video_thumbnail": str(thumb)}

    response = SimpleNamespace(text="done", images=[], videos=[Video()], media=[], metadata=["c_shared", "r_1"])
    client = _ParityClient(response)
    for module in (media_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", _no_op_async)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", _no_op_async)
        monkeypatch.setattr(module, "finalize_remote_chat_cleanup_from_response", _no_op_async)
        monkeypatch.setattr(module, "schedule_remote_chat_cleanup_from_response", lambda *_a, **_kw: None)
    monkeypatch.setattr(media_tools, "probe_playable_av_tracks", lambda path: frozenset({"video"}))
    monkeypatch.setattr(media_storage, "probe_playable_av_tracks", lambda path: frozenset({"video"}))
    mcp = MCPServer("video-main-and-preview")
    media_tools.register_media_tools(mcp)

    async def run():
        primary = (await mcp.call_tool("gemini_generate_media", {"background": False,
            "prompt": "ball", "media_type": "video", "output_dir": str(tmp_path / "primary"),
        })).content
        compact = await skill_server.create(background=False, prompt="ball", type="video", output_dir=str(tmp_path / "compact"))
        return primary, compact

    for content in asyncio.run(run()):
        result = _domain_payload(content)
        assert result["ok"] and result["data"]["state"] == "local"
        items = {item["kind"]: item for item in result["data"]["artifacts"]}
        assert items["video"]["mime_type"] == "video/mp4"
        assert items["image"]["mime_type"] == "image/jpeg"
        assert Path(items["video"]["local_path"]).read_bytes() == mp4
        assert Path(items["image"]["local_path"]).read_bytes() == jpeg
        assert items["video"]["id"] != items["image"]["id"]


def test_primary_and_compact_preserve_video_seen_only_in_earlier_stream_frame(monkeypatch, tmp_path):
    url = "https://example.test/current.mp4"
    video = GeneratedVideo(url=url)
    early = ModelOutput(metadata=["c_video", "r_one"], candidates=[Candidate(
        rcid="rc_one", text="working", generated_videos=[video],
    )])
    final = ModelOutput(metadata=["c_video", "r_one"], candidates=[Candidate(rcid="rc_one", text="done")])
    calls = []

    class Client(_ParityClient):
        async def generate_content_stream(self, **kwargs):
            calls.append(kwargs)
            yield early
            yield final

    async def save(_self, *, path, filename, verbose):
        destination = Path(path)
        destination.mkdir(parents=True, exist_ok=True)
        output = destination / "clip.mp4"
        output.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"\0" * 12)
        return {"video": str(output)}

    monkeypatch.setattr(GeneratedVideo, "save", save)
    client = Client(None)
    for module in (media_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", _no_op_async)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", _no_op_async)
        monkeypatch.setattr(module, "finalize_remote_chat_cleanup_from_response", _no_op_async)
        monkeypatch.setattr(module, "schedule_remote_chat_cleanup_from_response", lambda *_a, **_kw: None)
    monkeypatch.setattr(media_tools, "probe_playable_av_tracks", lambda path: frozenset({"video"}))
    monkeypatch.setattr(media_storage, "probe_playable_av_tracks", lambda path: frozenset({"video"}))
    mcp = MCPServer("video-frame-parity")
    media_tools.register_media_tools(mcp)

    async def run():
        primary = (await mcp.call_tool("gemini_generate_media", {"background": False,
            "prompt": "a blue ball", "media_type": "video", "output_dir": str(tmp_path / "primary"),
        })).content
        compact = await skill_server.create(background=False, prompt="a blue ball", type="video", output_dir=str(tmp_path / "compact"))
        return primary, compact

    for content in asyncio.run(run()):
        result = _domain_payload(content)
        assert result["ok"] and result["data"]["state"] == "local"
        artifact = result["data"]["artifacts"][0]
        assert artifact["kind"] == "video" and artifact["uri"] == url
    assert calls[0]["prompt"] == calls[1]["prompt"]
    assert calls[0]["timeout"] == calls[1]["timeout"] == 600


def test_music_local_evidence_supersedes_wrong_remote_url_extension():
    remote = artifact_from_remote(ArtifactKind.AUDIO, "https://example.test/audio.mp3")
    local = SimpleNamespace(uri=remote.uri, kind=ArtifactKind.VIDEO, state=ArtifactState.LOCAL)
    assert not media_storage.reconcile_remote_media_types((remote,), (local,))


def test_compact_create_and_edit_save_images_before_cleanup(monkeypatch, tmp_path):
    events = []
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABAQMAAADO7O3JAAAAIGNIUk0AAHomAACAhAAA+gAAAIDoAAB1MAAA6mAAADqYAAAXcJy6UTwAAAAGUExURf8AAP///0EdNBEAAAABYktHRAH/Ai3eAAAAB3RJTUUH6ggDByA1n7aAbwAAAApJREFUCNdjYAAAAAIAAeIhvDMAAAAASUVORK5CYII="
    )

    class Image:
        url = "https://example.test/image.png"
        title = "image"
        alt = "image"

        async def save(self, *, path, filename, verbose):
            events.append("save")
            dest = Path(path)
            dest.mkdir(parents=True, exist_ok=True)
            output = dest / filename
            output.write_bytes(png)
            return str(output)

    response = SimpleNamespace(text="done", images=[Image()], videos=[], media=[], metadata=["c_shared", "r_1"])
    client = _ParityClient(response)
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: client)
    monkeypatch.setattr(skill_server, "initialize_client", _no_op_async)
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", _no_op_async)

    async def finalize(*_args, **_kwargs):
        events.append("cleanup")

    monkeypatch.setattr(skill_server, "finalize_remote_chat_cleanup_from_response", finalize)
    reference = tmp_path / "reference.png"
    reference.write_bytes(png)

    async def run():
        created = await skill_server.create(background=False, prompt="image", output_dir=str(tmp_path / "create"), filename="out.png")
        edited = await skill_server.edit(background=False, image_path=str(reference), prompt="edit", output_dir=str(tmp_path / "edit"),
                                         filename="out.png")
        return created, edited

    created, edited = asyncio.run(run())
    assert events == ["save", "cleanup", "save", "cleanup"]
    for content in (created, edited):
        result = _domain_payload(content)
        assert result["data"]["artifacts"][0]["state"] == "local"
        assert Path(result["data"]["artifacts"][0]["local_path"]).read_bytes() == png
