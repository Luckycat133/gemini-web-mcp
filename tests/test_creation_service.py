"""Creation validates before authentication and recovers without a second start."""

import asyncio
from types import SimpleNamespace

import pytest

from src.domain import Artifact, ArtifactKind, ArtifactState, DomainErrorCode, OperationState
from src.services.creation import CreationRequest, CreationService
from src.services.artifacts import artifact_from_local_path
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
    assert result.meta.operation_state == OperationState.PARTIAL
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
