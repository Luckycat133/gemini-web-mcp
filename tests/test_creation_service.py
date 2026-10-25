"""Creation validates before authentication and recovers without a second start."""

import asyncio
from types import SimpleNamespace

import pytest
from gemini_webapi.types import Candidate, ChatHistory, ChatTurn, GeneratedImage, ModelOutput

from src.domain import Artifact, ArtifactKind, ArtifactState, CleanupState, DomainErrorCode, OperationState
from src.infrastructure.state_store import ArtifactLocator, StateStore
from src.services.creation import CreationRequest, CreationService
from src.services.artifacts import artifact_from_local_path
from src.services.operations import OperationContext, OperationService
from tests.media_fixtures import fake_finalize_generated_cleanup, write_audio, write_image, write_video


async def nothing(*_args, **_kwargs):
    pass


def sealed_provider():
    raise AssertionError("No provider access is allowed for this path")


@pytest.mark.parametrize("creation_request", [
    CreationRequest(" ", "music"),
    CreationRequest("test", "unknown"),
    CreationRequest("test", "image", filename="../outside.png"),
    CreationRequest("test", "image_edit"),
    CreationRequest("test", "image", image_path="/missing-reference.png"),
])
def test_invalid_creation_never_authenticates_or_allocates(creation_request):
    service = CreationService(client_provider=sealed_provider, initializer=sealed_provider)
    result = asyncio.run(service.generate(creation_request))
    assert not result.ok
    assert result.error.code == DomainErrorCode.INVALID_ARGUMENT


def record(**changes):
    values = dict(
        operation_id="op_opaque", operation_type="image", upstream_chat_id="c_owned",
        state=OperationState.QUEUED, artifacts=(), retain_chat=True, delete_after_seconds=None,
    )
    return SimpleNamespace(**{**values, **changes})


def sdk_history(text="", **candidate_fields):
    output = ModelOutput(metadata=["c_owned", "r_owned"], candidates=[
        Candidate(rcid="rc_owned", text=text, **candidate_fields),
    ])
    return ChatHistory(cid="c_owned", turns=[ChatTurn(role="model", text=text, model_output=output)])


async def no_music_cards(_client, cid):
    assert cid == "c_owned"
    return []


def install_synthetic_video_probe(monkeypatch, expected_path):
    # Control the AV probe only for the known decodable synthetic video.
    # Decoder availability itself is covered by the artifact tests.
    expected_video = write_video(expected_path.parent / "probe-reference.avi").read_bytes()

    def probe_video(path):
        assert path == str(expected_path.resolve())
        assert expected_path.read_bytes() == expected_video
        return {
            "streams": [{"codec_type": "video", "width": 16, "height": 16}],
            "format": {"duration": "1.0", "format_name": "avi"},
        }

    monkeypatch.setattr("src.services.artifacts._probe_av_metadata", probe_video)


def test_completed_artifact_remains_usable_without_remote_chat(tmp_path):
    path = write_image(tmp_path / "image.png")
    original = artifact_from_local_path(ArtifactKind.IMAGE, str(path), uri="https://example.test/image", source_chat_id="c_owned")
    service = CreationService(client_provider=sealed_provider, initializer=sealed_provider)
    recovered = asyncio.run(service.recover(record(state=OperationState.COMPLETED, artifacts=(original,))))
    assert recovered.ok
    assert recovered.data.state == ArtifactState.LOCAL
    assert recovered.data.artifacts[0].id == original.id
    assert recovered.data.artifacts[0].local_path == original.local_path


def test_unknown_source_is_not_guessed_or_regenerated():
    result = asyncio.run(CreationService(client_provider=sealed_provider).recover(record(upstream_chat_id=None)))
    assert not result.ok
    assert result.error.retryable is False
    assert result.meta.verification_status == "source_unobserved"


def test_missing_readback_preserves_queued_state_and_source(tmp_path):
    reads, cleanup = [], []

    class Client:
        async def read_chat(self, cid, limit):
            reads.append(cid)
            return None

        async def generate_content(self, **_kwargs):
            raise AssertionError("Recovery cannot start generation")

    async def finalize(response, **kwargs):
        cleanup.append(kwargs)
        return await fake_finalize_generated_cleanup(response, **kwargs)

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=finalize, recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(output_dir=str(tmp_path))))
    assert reads == ["c_owned"]
    assert result.meta.operation_state == OperationState.QUEUED
    assert result.data.source_chat_id == "c_owned"
    assert cleanup[0]["preserve_for_recovery"] is True
    assert cleanup[0]["retain_chat"] is True


@pytest.mark.parametrize("media_type", ["image", "video", "music"])
@pytest.mark.parametrize("text", ["", "The requested creation is complete."])
def test_sdk_model_turn_without_requested_media_remains_queued(monkeypatch, tmp_path, media_type, text):
    monkeypatch.setattr("src.services.media_generation.fetch_music_media_from_chat", no_music_cards)
    cleanup, starts = [], []
    history = sdk_history(text)

    class Client:
        async def read_chat(self, cid, limit):
            assert (cid, limit) == ("c_owned", 5)
            return history

        async def generate_content(self, **kwargs):
            starts.append(kwargs)
            raise AssertionError("Recovery cannot start generation")

    async def finalize(response, **kwargs):
        cleanup.append(kwargs)
        return await fake_finalize_generated_cleanup(response, **kwargs)

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=finalize,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(operation_type=media_type, output_dir=str(tmp_path), retain_chat=False)))
    assert result.ok
    assert result.data.state == ArtifactState.QUEUED
    assert result.data.artifacts == ()
    assert result.data.source_chat_id == "c_owned"
    assert result.meta.operation_state == OperationState.QUEUED
    assert result.meta.details["cleanup"].state == CleanupState.RETAINED
    assert cleanup[0]["preserve_for_recovery"] is True
    assert starts == []


@pytest.mark.parametrize("media_type", ["image", "video", "music"])
def test_empty_model_readback_does_not_complete_a_saved_preview(monkeypatch, tmp_path, media_type):
    monkeypatch.setattr("src.services.media_generation.fetch_music_media_from_chat", no_music_cards)
    kind, writer, suffix = {
        "image": (ArtifactKind.IMAGE, write_image, ".png"),
        "video": (ArtifactKind.VIDEO, write_video, ".mp4"),
        "music": (ArtifactKind.AUDIO, write_audio, ".mp3"),
    }[media_type]
    path = writer(tmp_path / f"preview{suffix}")
    saved = artifact_from_local_path(kind, str(path), source_chat_id="c_owned")

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return sdk_history("Done.")

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=fake_finalize_generated_cleanup,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(operation_type=media_type, artifacts=(saved,),
                                               output_dir=str(tmp_path), retain_chat=False)))
    assert result.ok
    assert result.data.state == ArtifactState.LOCAL
    assert result.data.artifacts[0].id == saved.id
    assert result.meta.operation_state == OperationState.QUEUED
    assert result.meta.details["cleanup"].state == CleanupState.RETAINED


@pytest.mark.parametrize("media_type,returned_kind", [
    ("video", "image"), ("video", "audio"), ("music", "image"), ("music", "video"),
])
def test_wrong_media_kind_cannot_complete_recovery(monkeypatch, tmp_path, media_type, returned_kind):
    monkeypatch.setattr("src.services.media_generation.fetch_music_media_from_chat", no_music_cards)
    downloads = []

    class Media:
        url = "https://example.test/auxiliary" if returned_kind != "audio" else ""
        mp3_url = "https://example.test/auxiliary-audio" if returned_kind == "audio" else ""

        async def save(self, path, filename, **kwargs):
            assert (media_type, returned_kind) == ("music", "video")
            destination = tmp_path / filename
            downloads.append(destination)
            return {"video": str(write_video(destination))}

    response = SimpleNamespace(metadata=["c_owned"], text="Done.", images=[], videos=[], media=[])
    setattr(response, "images" if returned_kind == "image" else "media", [Media()])

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=response)])

        async def generate_content(self, **_kwargs):
            raise AssertionError("Recovery cannot start generation")

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=fake_finalize_generated_cleanup,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(operation_type=media_type, output_dir=str(tmp_path), retain_chat=False)))
    assert result.ok
    assert result.data.state == ArtifactState.QUEUED
    assert result.meta.operation_state == OperationState.QUEUED
    assert result.meta.details["cleanup"].state == CleanupState.RETAINED
    if (media_type, returned_kind) == ("music", "video"):
        assert downloads
        assert len(result.data.artifacts) == 1
        assert result.data.artifacts[0].kind == ArtifactKind.VIDEO
        assert result.data.artifacts[0].local_path == str(downloads[0].resolve())
    else:
        assert downloads == []
        assert result.data.artifacts == ()


@pytest.mark.parametrize("failure", ["transport", "output_identity", "save"])
def test_recovery_pending_fallback_does_not_hide_real_errors(tmp_path, failure):
    finalizations = []

    class Video:
        # A malformed item cannot leave a persistent remote locator.
        url = ""

        async def save(self, **_kwargs):
            raise OSError("offline save failure")

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            if failure == "transport":
                raise ConnectionError("offline read failure")
            response = SimpleNamespace(metadata=["c_unrelated" if failure == "output_identity" else "c_owned"],
                                       images=[], videos=[Video()] if failure == "save" else [], media=[])
            return SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=response)])

    async def finalize(response, **kwargs):
        finalizations.append(kwargs)
        return await fake_finalize_generated_cleanup(response, **kwargs)

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=finalize,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(operation_type="video", output_dir=str(tmp_path), retain_chat=False)))
    assert not result.ok
    assert result.meta.operation_state == OperationState.FAILED
    assert result.data.source_chat_id == "c_owned"
    if failure == "save":
        assert result.meta.details["cleanup"].state == CleanupState.RETAINED
        assert finalizations[0]["preserve_for_recovery"] is True
    else:
        assert finalizations == []


def test_local_output_does_not_complete_a_still_queued_operation(tmp_path):
    path = write_image(tmp_path / "preview.png")
    original = artifact_from_local_path(ArtifactKind.IMAGE, str(path))

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return None

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=fake_finalize_generated_cleanup, recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(artifacts=(original,), output_dir=str(tmp_path))))
    assert result.data.artifacts[0].local_path == str(path.resolve())
    assert result.meta.operation_state == OperationState.QUEUED


def test_history_identity_mismatch_cannot_become_a_completed_operation(tmp_path):
    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return SimpleNamespace(cid="c_unrelated", turns=[])

    result = asyncio.run(CreationService(client_provider=Client, initializer=nothing).recover(record(output_dir=str(tmp_path))))
    assert not result.ok
    assert result.data.artifacts == ()
    assert result.data.source_chat_id == "c_owned"


def test_recovery_cannot_drop_an_unsaved_output_or_cleanup_its_source(tmp_path):
    path = write_image(tmp_path / "saved.png")
    saved = artifact_from_local_path(ArtifactKind.IMAGE, str(path))
    remote = Artifact(id="artifact_unsaved", kind=ArtifactKind.IMAGE, state=ArtifactState.REMOTE,
                      uri="https://example.test/unsaved", source_chat_id="c_owned")
    cleanup = []

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=SimpleNamespace(
                metadata=["c_owned"], text="Done", images=[], videos=[], media=[],
            ))])

    async def finalize(response, **kwargs):
        cleanup.append(kwargs)
        return await fake_finalize_generated_cleanup(response, **kwargs)

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=finalize, recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    result = asyncio.run(service.recover(record(artifacts=(saved, remote), retain_chat=False, output_dir=str(tmp_path))))
    assert {item.id for item in result.data.artifacts} == {saved.id, remote.id}
    assert result.meta.operation_state == OperationState.QUEUED
    assert cleanup[0]["preserve_for_recovery"] is True


def test_initialization_transport_failure_does_not_claim_generation_started():
    from curl_cffi.requests.exceptions import SSLError

    class Client:
        async def generate_content(self, **_kwargs):
            raise AssertionError("Initialization failed before generation")

    async def fail_initialization():
        raise SSLError("private proxy and account data")

    result = asyncio.run(CreationService(client_provider=Client, initializer=fail_initialization).generate(
        CreationRequest("test", "music"),
    ))
    assert not result.ok
    assert result.error.code == DomainErrorCode.NETWORK_ERROR
    assert result.meta.verification_status == "generation_not_started"
    assert result.meta.details["generation_attempted"] is False
    assert "private proxy" not in result.error.message


@pytest.mark.parametrize("media_type", ["image", "video"])
def test_repeated_queued_recovery_reuses_existing_output(tmp_path, media_type):
    downloads, cleanup = [], []

    class Media:
        url = "https://example.test/exact-output"

        async def save(self, path, filename, **kwargs):
            destination = tmp_path / filename
            downloads.append(destination)
            (write_image if media_type == "image" else write_video)(destination)
            return str(destination)

    response = SimpleNamespace(metadata=["c_owned"], queued=True, images=[], videos=[], media=[])
    setattr(response, "images" if media_type == "image" else "videos", [Media()])

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=response)])

    async def finalize(response, **kwargs):
        cleanup.append(kwargs)
        return await fake_finalize_generated_cleanup(response, **kwargs)

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=finalize,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    saved = record(operation_type=media_type, output_dir=str(tmp_path), retain_chat=False)

    async def recover_three_times():
        for _ in range(3):
            result = await service.recover(saved)
            saved.artifacts = result.data.artifacts
            saved.state = result.meta.operation_state
        return result

    result = asyncio.run(recover_three_times())
    assert len(downloads) == 1
    assert len(result.data.artifacts) == 1
    assert result.meta.operation_state == OperationState.QUEUED
    assert all(item["preserve_for_recovery"] for item in cleanup)
    assert result.data.artifacts[0].local_path == str(downloads[0].resolve())


@pytest.mark.parametrize("changed_bytes", [None, b"corrupted"])
def test_recovery_redownloads_missing_or_invalid_saved_output(tmp_path, changed_bytes):
    downloads = []

    class Image:
        url = "https://example.test/image"

        async def save(self, path, filename, **kwargs):
            destination = tmp_path / filename
            downloads.append(destination)
            return str(write_image(destination))

    response = SimpleNamespace(metadata=["c_owned"], queued=True, images=[Image()], videos=[], media=[])

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=response)])

    service = CreationService(client_provider=Client, initializer=nothing,
                              finalizer=fake_finalize_generated_cleanup,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    saved = record(output_dir=str(tmp_path))
    first = asyncio.run(service.recover(saved))
    saved.artifacts = first.data.artifacts
    if changed_bytes is None:
        downloads[0].unlink()
    else:
        downloads[0].write_bytes(changed_bytes)
    second = asyncio.run(service.recover(saved))
    assert len(downloads) == 2
    assert second.data.artifacts[0].verification.status.value == "verified"
    assert second.data.artifacts[0].id == first.data.artifacts[0].id
    if changed_bytes is not None:
        assert downloads[0].read_bytes() == changed_bytes


def test_partial_music_recovery_downloads_only_missing_companion(tmp_path):
    counts = {"audio": 0, "video": 0}

    class Music:
        mp3_url = "https://example.test/audio"
        url = "https://example.test/video"

        async def save(self, path, filename, download_type, **kwargs):
            counts[download_type] += 1
            if download_type == "video" and counts["video"] == 1:
                raise ConnectionError("Companion transfer interrupted")
            destination = tmp_path / filename
            (write_audio if download_type == "audio" else write_video)(destination)
            return {download_type: str(destination)}

    response = SimpleNamespace(metadata=["c_owned"], queued=True, images=[], videos=[], media=[Music()])

    class Client:
        async def read_chat(self, *_args, **_kwargs):
            return SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=response)])

    service = CreationService(client_provider=Client, initializer=nothing,
                              finalizer=fake_finalize_generated_cleanup,
                              recovery_artifact_recorder=lambda *_args, **_kwargs: None)
    saved = record(operation_type="music", output_dir=str(tmp_path))

    async def recover_three_times():
        for _ in range(3):
            result = await service.recover(saved)
            saved.artifacts = result.data.artifacts
            saved.state = result.meta.operation_state
        return result

    result = asyncio.run(recover_three_times())
    assert counts == {"audio": 1, "video": 2}
    assert len(result.data.artifacts) == 2
    assert result.meta.operation_state == OperationState.QUEUED


@pytest.mark.parametrize("queued", [False, True])
def test_music_recovery_merges_retained_audio_with_companion_only_readback(monkeypatch, tmp_path, queued):
    install_synthetic_video_probe(monkeypatch, tmp_path / "selected.mp4")
    counts, reads, cleanup, persisted = {"audio": 0, "video": 0}, [], [], []

    class Music:
        url = "https://example.test/companion-video"

        def __init__(self, with_audio):
            self.mp3_url = "https://example.test/retained-audio" if with_audio else ""

        async def save(self, path, filename, download_type, **kwargs):
            counts[download_type] += 1
            if download_type == "video" and counts["video"] == 1:
                raise ConnectionError("Offline companion transfer interrupted")
            destination = tmp_path / filename
            (write_audio if download_type == "audio" else write_video)(destination)
            return {download_type: str(destination)}

    initial = SimpleNamespace(metadata=["c_owned"], images=[], videos=[], media=[Music(True)])
    companion = SimpleNamespace(metadata=["c_owned"], images=[], videos=[], media=[Music(False)])
    if queued:
        companion.queued = True

    class Client:
        async def read_chat(self, cid, limit):
            assert (cid, limit) == ("c_owned", 5)
            reads.append(cid)
            response = initial if len(reads) == 1 else companion
            return SimpleNamespace(cid=cid, turns=[SimpleNamespace(role="model", model_output=response)])

        async def generate_content(self, **_kwargs):
            raise AssertionError("Recovery cannot start generation")

    def persist(_record, artifacts, **_kwargs):
        persisted[:] = artifacts

    async def finalize(response, **kwargs):
        observation = await fake_finalize_generated_cleanup(response, **kwargs)
        cleanup.append(observation.state)
        if observation.state == CleanupState.COMPLETED:
            assert len(persisted) == 2
            assert all(item.local_path and item.verification.status.value == "verified" for item in persisted)
        return observation

    service = CreationService(client_provider=Client, initializer=nothing, finalizer=finalize,
                              recovery_artifact_recorder=persist)
    saved = record(operation_type="music", output_dir=str(tmp_path), filename="selected.mp3", retain_chat=False)

    async def recover_twice():
        first = await service.recover(saved)
        assert first.ok
        assert first.meta.operation_state == OperationState.PARTIAL
        audio, video = first.data.artifacts
        assert (audio.kind, audio.state) == (ArtifactKind.AUDIO, ArtifactState.LOCAL)
        assert audio.verification.status.value == "verified"
        assert (video.kind, video.state) == (ArtifactKind.VIDEO, ArtifactState.REMOTE)
        saved.artifacts = tuple(ArtifactLocator.from_artifact(item) for item in first.data.artifacts)
        saved.state = first.meta.operation_state
        second = await service.recover(saved)
        assert {item.id for item in second.data.artifacts} == {audio.id, video.id}
        return second

    result = asyncio.run(recover_twice())
    assert result.ok
    assert result.data.state == ArtifactState.LOCAL
    assert result.meta.operation_state == (OperationState.QUEUED if queued else OperationState.COMPLETED)
    assert all(item.local_path and item.verification.status.value == "verified" for item in result.data.artifacts)
    assert counts == {"audio": 1, "video": 2}
    assert reads == ["c_owned", "c_owned"]
    assert cleanup == [CleanupState.RETAINED, CleanupState.RETAINED if queued else CleanupState.COMPLETED]


@pytest.mark.parametrize("media_type", ["video", "music"])
def test_restarted_media_recovery_retains_source_until_matching_output(monkeypatch, tmp_path, media_type):
    monkeypatch.setattr("src.services.media_generation.fetch_music_media_from_chat", no_music_cards)
    sources, starts, reads, downloads, cleanup = set(), [], [], [], []
    scope = "scope_offline"
    database = tmp_path / "operations.sqlite3"
    filename = "selected.mp4" if media_type == "video" else "selected.mp3"
    if media_type == "video":
        install_synthetic_video_probe(monkeypatch, tmp_path / filename)

    class Media:
        url = "https://example.test/requested-output" if media_type == "video" else ""
        mp3_url = "https://example.test/requested-output" if media_type == "music" else ""

        async def save(self, path, filename, **kwargs):
            destination = tmp_path / filename
            downloads.append(destination)
            (write_video if media_type == "video" else write_audio)(destination)
            return {media_type if media_type == "video" else "audio": str(destination)}

    completed = SimpleNamespace(metadata=["c_owned"], text="", images=[], videos=[], media=[])
    setattr(completed, "videos" if media_type == "video" else "media", [Media()])
    readbacks = [
        sdk_history(),
        sdk_history("The requested creation is complete.", generated_images=[
            GeneratedImage(url="https://example.test/unrelated-image.png"),
        ]),
        SimpleNamespace(cid="c_owned", turns=[SimpleNamespace(role="model", model_output=completed)]),
    ]

    class Client:
        async def generate_content(self, **kwargs):
            starts.append(kwargs)
            assert len(starts) == 1
            sources.add("c_owned")
            return SimpleNamespace(metadata=["c_owned"], queued=True, images=[], videos=[], media=[])

        async def read_chat(self, cid, limit):
            assert cid in sources
            assert limit == 5
            reads.append(cid)
            return readbacks[len(reads) - 1]

    client = Client()

    def operations():
        return OperationService(StateStore(database), scope_provider=lambda: scope)

    async def finalize(response, **kwargs):
        observation = await fake_finalize_generated_cleanup(response, **kwargs)
        cleanup.append(observation.state)
        if observation.state == CleanupState.COMPLETED:
            saved = StateStore(database).operations.get(scope, operation_id)
            assert saved.artifacts
            assert all(item.local_path and item.verification == "verified" for item in saved.artifacts)
            sources.remove("c_owned")
        return observation

    async def run():
        nonlocal operation_id
        first = operations()
        creation = CreationService(client_provider=lambda: client, initializer=nothing, cleanup_due=nothing,
                                   finalizer=finalize)

        async def generate(context):
            return await creation.generate(
                CreationRequest("offline fixture", media_type, output_dir=str(tmp_path), filename=filename),
                on_chat_observed=context.observe_chat_sync, on_artifacts_observed=context.observe_artifacts_sync,
            )

        started = await first.start(media_type, generate, output_dir=str(tmp_path), filename=filename, retain_chat=False)
        operation_id = started.data.operation_id
        await first._tasks[operation_id]
        for expected in (OperationState.QUEUED, OperationState.QUEUED, OperationState.COMPLETED):
            restarted = operations()

            def persist(saved_record, artifacts, **kwargs):
                OperationContext(restarted, saved_record).observe_artifacts_sync(artifacts, **kwargs)

            recovery = CreationService(client_provider=lambda: client, initializer=nothing, finalizer=finalize,
                                       recovery_artifact_recorder=persist)
            restarted.register_recovery(media_type, recovery.recover)
            result = await restarted.result(operation_id)
            assert result.ok
            assert result.data.operation_id == operation_id
            assert result.data.state == expected
            assert result.data.upstream_chat_id == "c_owned"
            assert len(starts) == 1
            if expected == OperationState.QUEUED:
                assert result.data.continuation_possible is True
                assert result.data.artifacts == ()
                assert sources == {"c_owned"}
                assert downloads == []
            else:
                assert len(result.data.artifacts) == 1
                artifact = result.data.artifacts[0]
                assert artifact.kind == (ArtifactKind.VIDEO if media_type == "video" else ArtifactKind.AUDIO)
                assert artifact.verification.status.value == "verified"
                assert artifact.local_path == str((tmp_path / filename).resolve())
                assert sources == set()
        assert (await operations().result(operation_id)).data.state == OperationState.COMPLETED

    operation_id = None
    asyncio.run(run())
    assert reads == ["c_owned"] * 3
    assert downloads == [tmp_path / filename]
    assert cleanup == [CleanupState.RETAINED] * 3 + [CleanupState.COMPLETED]
