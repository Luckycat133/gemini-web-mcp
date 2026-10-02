"""Cross-surface request, artifact materialization, and recovery regressions."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from gemini_webapi.types import Candidate, ModelOutput
from gemini_webapi.types.image import GeneratedImage, WebImage
import gemini_webapi.types.image as upstream_image
import gemini_webapi.types.video as upstream_video
from PIL import Image as PillowImage

import src.services.media_generation as generation
import src.skill_server as compact
import src.tools.media as primary
from src.domain import CleanupObservation, CleanupState
from src.infrastructure.rpc_contracts import get_contract
from src.services.artifacts import extract_response_artifacts
from tests.media_fixtures import fake_finalize_generated_cleanup, write_audio, write_image, write_video
from tests.test_media_tools import _FakeMediaClient, _call_tool, _make_mcp, _patch_media_env


class Image:
    def __init__(self, name="result", *, fail=False, pending=False):
        self.url = f"https://cdn.test/{name}.png"
        self.title = self.alt = name
        self.fail = fail
        self.pending = pending
        self.cancelled = False
        self.saves = []

    async def save(self, *, path, filename, verbose=False):
        self.saves.append((path, filename))
        if self.fail:
            raise PermissionError("fixture output directory denied")
        if self.pending:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True
        destination = Path(path).resolve()
        destination.mkdir(parents=True, exist_ok=True)
        name = filename if Path(filename).suffix else filename + ".png"
        return str(write_image(destination / name))


def patch_compact(monkeypatch, client, observations, *, cleanup_state=None):
    monkeypatch.setattr(compact, "get_gemini_client", lambda: client)

    async def no_op(*_args):
        return None

    async def finalize(response, **kwargs):
        observations.append({"response": response, **kwargs})
        observation = await fake_finalize_generated_cleanup(response, **kwargs)
        if cleanup_state is not None:
            return CleanupObservation(
                state=cleanup_state,
                upstream_chat_id=observation.upstream_chat_id,
                diagnostic_id="diag_cleanup_fixture",
                source="private fixture source",
                delete_at=123.0,
            )
        return observation

    monkeypatch.setattr(compact, "initialize_client", no_op)
    monkeypatch.setattr(compact, "cleanup_due_remote_chats", no_op)
    monkeypatch.setattr(compact, "finalize_generated_chat_cleanup", finalize)


def run_surface(surface, prompt, *, image_path=None, media_type="image"):
    async def run():
        if surface == "primary":
            return await _call_tool(
                _make_mcp(), "gemini_generate_media", prompt=prompt,
                media_type=media_type, image_path=image_path,
            )
        if surface == "edit":
            return await compact.edit(str(image_path), prompt)
        return await compact.create(prompt, type=media_type, image_path=image_path)

    return asyncio.run(run())


def test_primary_and_compact_share_request_and_return_verified_local_images(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    client = _FakeMediaClient(images=[Image()])
    primary_observations = []
    compact_observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=primary_observations)
    patch_compact(monkeypatch, client, compact_observations)

    first = run_surface("primary", "blue bicycle")[0].meta["domain_result"]
    primary_request = client.captured_generate_kwargs
    second = run_surface("compact", "blue bicycle")[0].meta["domain_result"]

    assert primary_request == client.captured_generate_kwargs
    assert primary_request["media_mode"] == "image"
    for result in (first, second):
        assert result["ok"] is True
        assert result["meta"]["operation_state"] == "completed"
        artifact = result["data"]["artifacts"][0]
        assert artifact["state"] == "local"
        assert artifact["verification"]["status"] == "verified"
        assert Path(artifact["local_path"]).is_file()
        assert result["meta"]["details"]["cleanup"]["state"] == "completed"
    assert first["data"]["artifacts"][0]["id"] == second["data"]["artifacts"][0]["id"]
    for decision in (*primary_observations, *compact_observations):
        assert decision["owns_chat"] is True
        assert decision["preserve_for_recovery"] is False
    assert len(client.last_response.images[0].saves) == 2


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_failed_save_retains_remote_image_and_chat(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    image_path = write_image(tmp_path / "reference.png")
    client = _FakeMediaClient(images=[Image(fail=True)])
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)

    result = run_surface(surface, "blue bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert result["data"]["state"] == "remote"
    assert result["data"]["artifacts"][0]["uri"] == "https://cdn.test/result.png"
    assert result["meta"]["operation_state"] == "partial"
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"
    assert observations[0]["owns_chat"] is True
    assert observations[0]["preserve_for_recovery"] is True


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_cleanup_failure_preserves_verified_generation_state(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    image_path = write_image(tmp_path / "reference.png")
    client = _FakeMediaClient(images=[Image()])
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations, cleanup_state=CleanupState.FAILED)
    if surface == "primary":
        monkeypatch.setattr(primary, "finalize_generated_chat_cleanup", compact.finalize_generated_chat_cleanup)

    result = run_surface(surface, "blue bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert result["ok"] is True
    assert result["meta"]["operation_state"] == "completed"
    assert result["data"]["state"] == "local"
    cleanup = result["meta"]["details"]["cleanup"]
    assert cleanup["state"] == "failed"
    assert cleanup["diagnostic_id"] == "diag_cleanup_fixture"
    assert "source" not in cleanup
    assert "delete_at" not in cleanup


@pytest.mark.parametrize("surface", ["compact", "edit"])
def test_compact_partial_save_timeout_preserves_all_uris_and_completed_file(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    image_path = write_image(tmp_path / "reference.png")
    deadlines = []
    actual_timeout = asyncio.timeout

    def phase_timeout(_delay):
        deadline = actual_timeout(None)
        deadlines.append(deadline)
        return deadline

    class PendingImage(Image):
        async def save(self, **kwargs):
            # The first artifact has already completed download and verification
            # before this phase. Expire the real timeout only after reaching it.
            deadlines[0].reschedule(asyncio.get_running_loop().time())
            return await super().save(**kwargs)

    first, pending = Image("first"), PendingImage("pending", pending=True)
    client = _FakeMediaClient(images=[first, pending])
    observations = []
    patch_compact(monkeypatch, client, observations)
    monkeypatch.setattr(compact.asyncio, "timeout", phase_timeout)

    result = run_surface(surface, "blue bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert result["error"]["code"] == "TIMED_OUT"
    assert result["data"]["source_chat_id"] == "c_media1"
    artifacts = {artifact["uri"]: artifact for artifact in result["data"]["artifacts"]}
    assert set(artifacts) == {first.url, pending.url}
    assert artifacts[first.url]["verification"]["status"] == "verified"
    assert Path(artifacts[first.url]["local_path"]).is_file()
    assert artifacts[pending.url]["state"] == "remote"
    assert pending.cancelled is True
    assert observations[0]["preserve_for_recovery"] is True
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"


def test_compact_music_with_unsaved_video_keeps_chat_even_when_audio_is_verified(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Music:
        title = "song"
        mp3_url = "https://cdn.test/song.mp3"
        url = "https://cdn.test/song.mp4"

        async def save(self, *, path, filename, download_type, verbose=False):
            if download_type == "video":
                raise PermissionError("fixture video save denied")
            destination = Path(path).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            return {"audio": str(write_audio(destination / filename))}

    client = _FakeMediaClient(media=[Music()])
    observations = []
    patch_compact(monkeypatch, client, observations)

    result = run_surface("compact", "song", media_type="music")[0].meta["domain_result"]

    artifacts = {artifact["kind"]: artifact for artifact in result["data"]["artifacts"]}
    assert artifacts["audio"]["state"] == "local"
    assert artifacts["audio"]["verification"]["status"] == "verified"
    assert artifacts["video"]["state"] == "remote"
    assert result["meta"]["operation_state"] == "partial"
    assert observations[0]["preserve_for_recovery"] is True


def test_compact_recovers_and_materializes_music_in_same_deadline(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class Music:
        title = "song"
        mp3_url = "https://cdn.test/song.mp3"
        url = ""

        async def save(self, *, path, filename, download_type, verbose=False):
            assert download_type == "audio"
            destination = Path(path).resolve()
            destination.mkdir(parents=True, exist_ok=True)
            return {"audio": str(write_audio(destination / filename))}

    client = _FakeMediaClient(response_text="accepted", response_status="processing")
    observations = []
    patch_compact(monkeypatch, client, observations)

    async def fetch_music(actual_client, cid):
        assert actual_client is client
        assert cid == "c_media1"
        return [Music()]

    monkeypatch.setattr(generation, "fetch_music_media_from_chat", fetch_music)

    result = run_surface("compact", "song", media_type="music")[0].meta["domain_result"]

    assert result["ok"] is True
    assert result["data"]["state"] == "local"
    assert result["data"]["artifacts"][0]["verification"]["status"] == "verified"
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"
    assert result["meta"]["operation_state"] == "queued"
    assert observations[0]["preserve_for_recovery"] is True
    assert client.captured_generate_kwargs["media_mode"] == "music"


@pytest.mark.parametrize("surface", ["primary", "compact"])
@pytest.mark.parametrize("mode,error_code", [
    ("transport", "NETWORK_ERROR"),
    ("http_503", "NETWORK_ERROR"),
    ("http_429", "RATE_LIMITED"),
    ("rejected", "UPSTREAM_REJECTED"),
    ("missing_envelope", "UPSTREAM_CHANGED"),
    ("invalid_json", "UPSTREAM_CHANGED"),
    ("changed_shape", "UPSTREAM_CHANGED"),
    ("invalid_card_url", "UPSTREAM_CHANGED"),
    ("blank_card_url", "UPSTREAM_CHANGED"),
    ("missing_card_url", "UPSTREAM_CHANGED"),
    ("valid_empty", "ARTIFACT_NOT_RETURNED"),
    ("success", None),
])
def test_real_music_readback_distinguishes_empty_from_unavailable_and_preserves_chat(
    monkeypatch, tmp_path, caplog, surface, mode, error_code,
):
    monkeypatch.chdir(tmp_path)
    contract = get_contract("media.music_chat")
    fixtures = json.loads((Path(__file__).parent / "fixtures" / "rpc_management_cases.json").read_text())["music_chat"]["cases"]
    calls = []

    class Client(_FakeMediaClient):
        async def _batch_execute(self, requests, **kwargs):
            calls.append(requests)
            assert requests[0].rpcid == contract.rpc_id
            assert kwargs["close_on_error"] is False
            if mode == "transport":
                raise ConnectionError("sealed-sensitive-exception-marker")
            rpc_id = "offline_other_rpc" if mode == "missing_envelope" else contract.rpc_id
            body = {} if mode == "changed_shape" else [[]]
            if mode in {"invalid_card_url", "blank_card_url", "missing_card_url", "success"}:
                body = json.loads(json.dumps(fixtures["success"]["body"]))
                if mode != "success":
                    body[0][0][3][0][0][12][0]["87"][0][1][7][1] = {
                        "invalid_card_url": 123, "blank_card_url": "  ", "missing_card_url": None,
                    }[mode]
            part = ["wrb.fr", rpc_id, json.dumps(body), None, None, [7] if mode == "rejected" else None]
            return SimpleNamespace(
                status_code=503 if mode == "http_503" else 429 if mode == "http_429" else 200,
                text="sealed-sensitive-response-marker" if mode == "invalid_json" else json.dumps([part]),
            )

    client = Client(response_text="accepted", media=[])
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)
    # Both surfaces must traverse the actual shared RPC service and parser.
    monkeypatch.setattr(primary, "_fetch_music_media_from_chat", generation.fetch_music_media_from_chat)
    if mode == "success":
        audio = write_audio(tmp_path / "source.wav").read_bytes()

        class Session:
            def __init__(self, **_kwargs):
                pass

            async def get(self, url, **_kwargs):
                assert url == "https://cdn.example/song.mp3"
                return SimpleNamespace(status_code=200, content=audio, headers={})

            async def close(self):
                pass

        monkeypatch.setattr(upstream_video, "AsyncSession", Session)

    result = run_surface(surface, "song", media_type="music")[0].meta["domain_result"]

    assert len(calls) == 1
    assert result["data"]["source_chat_id"] == "c_media1"
    if mode == "success":
        assert result["ok"] is True
        assert result["data"]["state"] == "local"
        assert result["data"]["artifacts"][0]["verification"]["status"] == "verified"
        assert result["meta"]["details"]["cleanup"]["state"] == "completed"
        assert observations[0]["preserve_for_recovery"] is False
    elif mode == "valid_empty":
        assert result["ok"] is False
        assert result["error"]["code"] == error_code
        assert result["data"]["state"] == "empty"
        assert observations[0]["preserve_for_recovery"] is False
        assert result["meta"]["details"]["cleanup"]["state"] == "completed"
    else:
        assert result["ok"] is False
        assert result["error"]["code"] == error_code
        assert result["data"]["state"] == "failed"
        assert result["meta"]["diagnostic_id"]
        assert observations[0]["preserve_for_recovery"] is True
        assert result["meta"]["details"]["cleanup"]["state"] == "retained"
    assert "sealed-sensitive-exception-marker" not in caplog.text
    assert "sealed-sensitive-response-marker" not in caplog.text
    assert "sealed-sensitive" not in json.dumps(result)


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_queued_request_with_verified_ready_artifact_keeps_chat_and_pending_operation(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    image_path = write_image(tmp_path / "reference.png")
    client = _FakeMediaClient(images=[Image()], response_status="processing")
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)

    content = run_surface(surface, "blue bicycle", image_path=str(image_path))
    result = content[0].meta["domain_result"]

    assert result["ok"] is True
    assert result["data"]["state"] == "local"
    assert result["data"]["artifacts"][0]["verification"]["status"] == "verified"
    assert result["meta"]["operation_state"] == "queued"
    assert result["meta"]["details"]["upstream_queued"] is True
    assert observations[0]["preserve_for_recovery"] is True
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"


@pytest.mark.parametrize("surface", ["primary", "compact"])
def test_music_direct_audio_uri_does_not_require_unavailable_fallback(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)

    class Client(_FakeMediaClient):
        async def generate_content(self, **kwargs):
            response = await super().generate_content(**kwargs)
            response.audio_url = "https://cdn.test/direct.wav"
            return response

        async def _batch_execute(self, *_args, **_kwargs):
            raise AssertionError("An already observed audio URI must not require fallback")

    client = Client()
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)

    result = run_surface(surface, "song", media_type="music")[0].meta["domain_result"]

    assert result["ok"] is True
    assert result["data"]["state"] == "remote"
    assert result["data"]["artifacts"][0]["uri"] == "https://cdn.test/direct.wav"
    assert observations[0]["preserve_for_recovery"] is True
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"


@pytest.mark.parametrize("surface", ["primary", "compact"])
def test_music_visualization_without_required_audio_keeps_observed_output_and_chat(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    video = write_video(tmp_path / "source.avi").read_bytes()

    class Session:
        def __init__(self, **_kwargs):
            pass

        async def get(self, url, **_kwargs):
            assert url == "https://cdn.test/visualization.mp4"
            return SimpleNamespace(status_code=200, content=video, headers={})

        async def close(self):
            pass

    monkeypatch.setattr(upstream_video, "AsyncSession", Session)
    client = _FakeMediaClient(media=[upstream_video.GeneratedMedia(url="https://cdn.test/visualization.mp4")])
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)

    result = run_surface(surface, "song", media_type="music")[0].meta["domain_result"]

    assert result["ok"] is False
    assert result["error"]["code"] == "ARTIFACT_NOT_RETURNED"
    assert result["data"]["state"] == "empty"
    assert result["data"]["artifacts"][0]["kind"] == "video"
    assert result["data"]["artifacts"][0]["local_path"]
    assert result["meta"]["details"]["save_failure_count"] == 0
    assert observations[0]["preserve_for_recovery"] is True
    assert result["meta"]["details"]["cleanup"]["state"] == "retained"


@pytest.mark.parametrize("media_kind", ["audio", "music_video", "video"])
def test_real_sdk_media_save_does_not_write_unrequested_thumbnails(monkeypatch, tmp_path, media_kind):
    audio = write_audio(tmp_path / "source.wav").read_bytes()
    video = write_video(tmp_path / "source.avi").read_bytes()
    existing_name = {"audio": "song.mp3_audio_thumb", "music_video": "song.mp4_video_thumb", "video": "song.jpg"}[media_kind]
    existing = tmp_path / existing_name
    original = b"Existing unrelated file must survive"
    existing.write_bytes(original)
    requested = []
    sessions = []

    class Session:
        def __init__(self, **kwargs):
            self.closed = False
            sessions.append(self)

        async def get(self, url, **kwargs):
            requested.append(url)
            assert "thumbnail" not in url
            return SimpleNamespace(status_code=200, content=audio if media_kind == "audio" else video, headers={})

        async def close(self):
            self.closed = True

    monkeypatch.setattr(upstream_video, "AsyncSession", Session)
    if media_kind == "audio":
        media = upstream_video.GeneratedMedia(
            url="", mp3_url="https://cdn.test/song.mp3", mp3_thumbnail="https://cdn.test/audio-thumbnail",
        )
    elif media_kind == "music_video":
        media = upstream_video.GeneratedMedia(url="https://cdn.test/song.mp4", thumbnail="https://cdn.test/video-thumbnail")
    else:
        media = upstream_video.GeneratedVideo(url="https://cdn.test/song.mp4", thumbnail="https://cdn.test/video-thumbnail")

    outcome = asyncio.run(generation.save_generated_media(
        SimpleNamespace(media=[media], videos=[media]),
        media_type="music" if media_kind != "video" else "video",
        output_dir=str(tmp_path), filename="song", prompt="sealed fixture",
        requested_model="flash", request_model="gemini-3-flash", effective_backend="fixture",
        observed_backend=None, source_chat_id="c_offline_fixture",
    ))

    assert existing.read_bytes() == original
    assert len(requested) == 1
    assert len(outcome.artifacts) == 1
    assert outcome.artifacts[0].local_path
    assert all(session.closed for session in sessions)
    if media_kind == "audio":
        assert media.mp3_thumbnail.endswith("audio-thumbnail")
        assert outcome.artifacts[0].verification.status.value == "verified"
    else:
        assert media.thumbnail.endswith("video-thumbnail")


@pytest.mark.parametrize("operation", ["create", "edit"])
def test_compact_initialization_is_in_total_operation_deadline(monkeypatch, tmp_path, operation):
    cancelled = []
    client = _FakeMediaClient(images=[Image()])
    observations = []
    patch_compact(monkeypatch, client, observations)
    monkeypatch.setattr(compact, "media_operation_timeout", lambda *_args: 0.04)

    async def initialize():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(compact, "initialize_client", initialize)
    image_path = write_image(tmp_path / "reference.png")
    result = run_surface("edit" if operation == "edit" else "compact", "bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert result["error"]["code"] == "TIMED_OUT"
    assert cancelled == [True]
    assert client.captured_generate_kwargs is None
    assert observations == []


@pytest.mark.parametrize("surface", ["compact", "edit"])
def test_compact_presentation_failure_preserves_verified_generation_and_cleanup(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    image_path = write_image(tmp_path / "reference.png")
    client = _FakeMediaClient(images=[Image()])
    observations = []
    patch_compact(monkeypatch, client, observations)

    def fail_to_format(*_args, **_kwargs):
        raise RuntimeError("fixture formatting failure")

    monkeypatch.setattr(compact, "_format_response", fail_to_format)
    result = run_surface(surface, "bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert result["ok"] is True
    assert result["data"]["state"] == "local"
    assert result["data"]["artifacts"][0]["verification"]["status"] == "verified"
    assert result["data"]["source_chat_id"] == "c_media1"
    assert any(item["code"] == "PRESENTATION_FAILED" for item in result["warnings"])
    assert observations[0]["preserve_for_recovery"] is False
    assert result["meta"]["details"]["cleanup"]["state"] == "completed"


@pytest.mark.parametrize("surface,media_type,mode", [
    ("primary", "image", "image"), ("compact", "image", "image"),
    ("edit", "image_edit", "image"),
    ("primary", "music", "music"), ("compact", "music", "music"),
    ("primary", "video", "video"), ("compact", "video", "video"),
])
def test_surfaces_select_only_observed_native_modes(monkeypatch, tmp_path, surface, media_type, mode):
    client = _FakeMediaClient()
    _patch_media_env(monkeypatch, client)
    patch_compact(monkeypatch, client, [])
    image_path = write_image(tmp_path / "reference.png") if surface == "edit" else None

    run_surface(surface, "fixture media", image_path=image_path, media_type=media_type)

    assert client.captured_generate_kwargs.get("media_mode") == mode


class SDKOutputClient(_FakeMediaClient):
    def __init__(self, output):
        super().__init__()
        self.output = output

    async def generate_content(self, **kwargs):
        self.captured_generate_kwargs = dict(kwargs)
        self.last_response = self.output
        return self.output


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_real_sdk_web_image_only_output_is_not_creation_success(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    reference = WebImage(url="https://web.test/reference.png")
    response = ModelOutput(metadata=["c_reference", "r_reference"], candidates=[
        Candidate(rcid="rc_reference", text="Search reference", web_images=[reference]),
    ])
    saved = []

    async def forbidden_save(self, **kwargs):
        saved.append(self.url)
        raise AssertionError("Web references must not be saved as generated images")

    monkeypatch.setattr(WebImage, "save", forbidden_save)
    client = SDKOutputClient(response)
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)
    image_path = write_image(tmp_path / "reference-input.png")

    result = run_surface(surface, "bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert result["ok"] is False
    assert result["error"]["code"] == "ARTIFACT_NOT_RETURNED"
    assert result["data"]["state"] == "empty"
    assert result["data"]["artifacts"] == []
    assert result["meta"]["details"]["cleanup"]["state"] == "completed"
    assert observations[0]["preserve_for_recovery"] is False
    assert saved == []
    assert not (tmp_path / "generated_media").exists()
    # The original SDK output and ordinary assistance extraction retain it.
    assert response.images == [reference]
    assert extract_response_artifacts(response)[0].uri == reference.url


@pytest.mark.parametrize("surface", ["primary", "compact", "edit"])
def test_real_generated_image_full_size_mutation_has_one_stable_local_artifact(monkeypatch, tmp_path, surface):
    monkeypatch.chdir(tmp_path)
    original_uri = "https://generated.test/created.png=s1024-rj"
    generated = GeneratedImage(url=original_uri)
    reference = WebImage(url="https://web.test/reference.png")
    response = ModelOutput(metadata=["c_created", "r_created"], candidates=[
        Candidate(rcid="rc_created", text="Generated", web_images=[reference], generated_images=[generated]),
    ])
    image_path = write_image(tmp_path / "reference-input.png")
    image_bytes = image_path.read_bytes()
    downloads = []
    sessions = []

    class Session:
        def __init__(self, **kwargs):
            self.closed = False
            sessions.append(self)

        async def get(self, url, **kwargs):
            downloads.append(url)
            return SimpleNamespace(status_code=200, headers={"content-type": "image/png"}, content=image_bytes)

        async def close(self):
            self.closed = True

    monkeypatch.setattr(upstream_image, "AsyncSession", Session)
    client = SDKOutputClient(response)
    observations = []
    _patch_media_env(monkeypatch, client, captured_schedule=observations)
    patch_compact(monkeypatch, client, observations)

    result = run_surface(surface, "bicycle", image_path=str(image_path))[0].meta["domain_result"]

    assert generated.url == "https://generated.test/created.png=s2048-rj"
    assert downloads == [generated.url]
    assert all(session.closed for session in sessions)
    assert result["data"]["state"] == "local"
    artifacts = result["data"]["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["uri"] == original_uri
    assert artifacts[0]["verification"]["status"] == "verified"
    assert Path(artifacts[0]["local_path"]).read_bytes() == image_bytes
    assert observations[0]["preserve_for_recovery"] is False
    assert result["meta"]["details"]["cleanup"]["state"] == "completed"


def save_fixture_image(image, destination, *, filename="same.png"):
    return generation.save_generated_media(
        SimpleNamespace(images=[image]), media_type="image", output_dir=str(destination),
        filename=filename, prompt="same", requested_model="flash", request_model="gemini-3-flash",
        effective_backend="fixture", observed_backend=None, source_chat_id="c_fixture",
    )


def test_concurrent_named_saves_reserve_distinct_paths_and_preserve_each_image(tmp_path):
    async def run():
        entered = 0
        release = asyncio.Event()

        class Picture(Image):
            def __init__(self, width):
                super().__init__(str(width))
                self.width = width

            async def save(self, *, path, filename, verbose=False):
                nonlocal entered
                entered += 1
                if entered == 2:
                    release.set()
                await release.wait()
                target = Path(path) / filename
                PillowImage.new("RGB", (self.width, 1)).save(target, format="PNG")
                return str(target)

        first, second = await asyncio.gather(
            save_fixture_image(Picture(2), tmp_path), save_fixture_image(Picture(3), tmp_path),
        )
        return first.artifacts[0], second.artifacts[0]

    first, second = asyncio.run(run())
    assert first.local_path != second.local_path
    assert (first.width, second.width) == (2, 3)
    assert {path.name for path in tmp_path.iterdir()} == {"same.png", "same_2.png"}


def test_failed_save_releases_its_reservation_without_removing_existing_output(tmp_path):
    existing = write_image(tmp_path / "existing.png")
    existing_bytes = existing.read_bytes()

    async def run():
        failed = await save_fixture_image(Image(fail=True), tmp_path)
        assert failed.failures
        assert not (tmp_path / "same.png").exists()
        return await save_fixture_image(Image(), tmp_path)

    saved = asyncio.run(run())
    assert saved.artifacts[0].local_path == str(tmp_path / "same.png")
    assert existing.read_bytes() == existing_bytes


def test_cancelled_save_removes_only_its_unreturned_partial_file(tmp_path):
    existing = write_image(tmp_path / "existing.png")
    existing_bytes = existing.read_bytes()

    async def run():
        entered = asyncio.Event()

        class PartialImage(Image):
            async def save(self, *, path, filename, verbose=False):
                write_image(Path(path) / filename)
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(save_fixture_image(PartialImage(), tmp_path))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert {path.name for path in tmp_path.iterdir()} == {"existing.png"}
        return await save_fixture_image(Image(), tmp_path)

    saved = asyncio.run(run())
    assert saved.artifacts[0].local_path == str(tmp_path / "same.png")
    assert existing.read_bytes() == existing_bytes


def test_concurrent_suffix_normalization_does_not_replace_another_reserved_output(tmp_path):
    async def run():
        entered = 0
        release = asyncio.Event()

        class JPEG(Image):
            def __init__(self, width):
                super().__init__(str(width))
                self.width = width

            async def save(self, *, path, filename, verbose=False):
                nonlocal entered
                entered += 1
                if entered == 2:
                    release.set()
                await release.wait()
                target = Path(path) / filename
                PillowImage.new("RGB", (self.width, 1)).save(target, format="JPEG")
                return str(target)

        return await asyncio.gather(
            save_fixture_image(JPEG(2), tmp_path, filename="same.png"),
            save_fixture_image(JPEG(3), tmp_path, filename="same.jpg"),
        )

    first, second = asyncio.run(run())
    assert first.artifacts[0].local_path != second.artifacts[0].local_path
    assert (first.artifacts[0].width, second.artifacts[0].width) == (2, 3)
    assert {path.name for path in tmp_path.iterdir()} == {"same_2.jpg", "same.jpg"}
