"""Recovery never regenerates or credits a different turn or candidate."""

import asyncio
import base64
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.constants import GEMINI_GENERIC_FAILURE_REPLY
from src.domain import Artifact, ArtifactKind, ArtifactState
from src.services import media_jobs


@pytest.fixture(autouse=True)
def _synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-test-account")


class Store:
    def __init__(self):
        self.events = []
        self.owner = True

    def is_worker_owner(self, *_):
        return self.owner

    def finish_worker(self, operation_id, **kwargs):
        self.events.append(("finish", kwargs))
        return self.owner

    def save_media_artifacts(self, operation_id, artifacts, **kwargs):
        self.events.append(("saved", artifacts))
        return self.owner

    def release_media_cleanup(self, operation_id, **kwargs):
        self.events.append(("release", operation_id))
        return self.owner

    def update_cleanup(self, operation_id, state):
        self.events.append(("cleanup", state))


def _record():
    from src.infrastructure.account_identity import current_account_key
    return SimpleNamespace(account_key=current_account_key(), operation_id="op_one", operation="generate", media_type="image", output_dir=None,
                           filename="op_one", requested_model="flash", request_model="flash",
                           effective_backend="image", retain_chat=False, delete_after_seconds=None,
                           upstream_chat_id="c_one", upstream_response_id="r_one", upstream_candidate_id="rc_one")


def _response(rid="r_one", rcid="rc_one"):
    candidate = SimpleNamespace(rcid=rcid, generated_images=[object()], generated_videos=[], generated_media=[])
    return SimpleNamespace(metadata=["c_one", rid], candidates=[candidate], chosen=0)


def _prepare(monkeypatch, history):
    import src.client_wrapper as wrapper
    client = SimpleNamespace(read_chat=AsyncMock(return_value=history))
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=SimpleNamespace(state=SimpleNamespace(value="completed"))))
    return client


def test_recover_none_and_wrong_turn_keep_guard(monkeypatch):
    for history in (None, SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=_response("r_old"))]),
                    SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=_response(rcid="rc_old"))])):
        client = _prepare(monkeypatch, history)
        store = Store()
        asyncio.run(media_jobs.recover_media_job(_record(), store=store, worker_token="worker"))
        assert store.events[0][1]["phase"] == ("media_readback_unverified" if history is None else "media_identity_unverified")
        assert all(event[0] != "release" for event in store.events)
        client.read_chat.assert_awaited_once()


def test_recover_saves_local_metadata_before_releasing_guard(monkeypatch):
    history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=_response())])
    _prepare(monkeypatch, history)
    artifact = Artifact(id="id", kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL,
                        uri="https://example.invalid/signed", title="response content", local_path="/tmp/image.png")
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(
        return_value=SimpleNamespace(artifacts=(artifact,), failures=())))
    store = Store()
    asyncio.run(media_jobs.recover_media_job(_record(), store=store, worker_token="worker"))
    assert [event[0] for event in store.events] == ["saved", "release", "cleanup", "finish"]
    assert store.events[0][1][0].uri is None
    assert store.events[0][1][0].title is None
    assert store.events[-1][1]["state"] == "completed"


def test_thumbnail_alone_never_completes_video(monkeypatch):
    _prepare(monkeypatch, SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=_response())]))
    artifact = Artifact(id="id", kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL, local_path="/tmp/thumb.jpg")
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(
        return_value=SimpleNamespace(artifacts=(artifact,), failures=())))
    store = Store()
    record = _record()
    record.media_type = "video"
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="worker"))
    assert [event[0] for event in store.events] == ["finish"]
    assert store.events[0][1]["phase"] == "media_artifact_not_observed"


def test_expired_web_auth_fails_before_video_generation(monkeypatch, tmp_path):
    from src import client_wrapper
    from src.client_manager import ClientAuthenticationRequired
    from src.infrastructure.operation_store import OperationStore

    store = OperationStore()
    store.create_or_get("job", "gemini_generate_video", media_type="video", output_dir=str(tmp_path),
                        filename="job", request_model="gemini-3-flash", effective_backend="Gemini Web video mode")
    assert store.claim_worker("job", "worker")
    generate = AsyncMock()
    monkeypatch.setattr(media_jobs, "generate_media_response", generate)
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: object())
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock(side_effect=ClientAuthenticationRequired()))
    asyncio.run(media_jobs.execute_media_job(
        {"prompt": "A synthetic scene", "media_type": "video", "output_dir": str(tmp_path)},
        operation_id="job", store=store, worker_token="worker",
    ))
    saved = store.get("job")
    assert saved.state == "failed" and saved.error_code == "AUTH_REQUIRED"
    assert not saved.cleanup_protected and saved.upstream_chat_id is None
    generate.assert_not_awaited()


@pytest.mark.parametrize(("media_type", "client_supports_native", "options", "prompt", "expected", "native"), [
    pytest.param("video", True, {}, "  Rotate a paper cube.\n", "  Rotate a paper cube.\n", True,
                 id="native-video-raw"),
    pytest.param("music", True, {}, "  Play a soft piano phrase.\n", "  Play a soft piano phrase.\n", True,
                 id="native-music-raw"),
    pytest.param("video", True, {"web_media_transport": "native"},
                 "Generate a video using Gemini's video generation capability. Prompt: A literal phrase.",
                 "Generate a video using Gemini's video generation capability. Prompt: A literal phrase.", True,
                 id="native-preserves-literal-sdk-prefix"),
    pytest.param("video", True, {"web_media_transport": "sdk"}, "  Rotate a paper cube.\n",
                 "Generate a video using Gemini's video generation capability. Prompt: Rotate a paper cube.", False,
                 id="explicit-sdk-keeps-video-prefix"),
    pytest.param("music", False, {}, "  Play a soft piano phrase.\n",
                 "Create music/audio using Gemini's music generation capability. Prompt: Play a soft piano phrase.",
                 False, id="legacy-client-keeps-music-prefix"),
    pytest.param("image", True, {}, "  Draw a paper cube.\n", "Generate an image. Prompt: Draw a paper cube.", False,
                 id="image-keeps-prefix"),
    pytest.param("image", True, {"edit": True}, "  Add a blue shadow.\n", "Edit this image:   Add a blue shadow.\n",
                 False, id="image-edit-keeps-prefix"),
])
def test_worker_prompt_is_unchanged_only_on_native_media_route(
    monkeypatch, tmp_path, media_type, client_supports_native, options, prompt, expected, native,
):
    from src import client_wrapper
    from src.infrastructure.operation_store import OperationStore
    from src.services import native_media_generation

    class LegacyClient:
        def video_generation_scope(self):
            return nullcontext()

    class PolicyClient(LegacyClient):
        def _resolve_policy_model(self, model):
            raise AssertionError("The native browser adapter is mocked.")

    monkeypatch.delenv("GEMINI_WEB_MEDIA_TRANSPORT", raising=False)
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create_or_get("job", "gemini_create_media")
    assert store.claim_worker("job", "worker")
    client = PolicyClient() if client_supports_native else LegacyClient()
    client.generate_content = AsyncMock(return_value=SimpleNamespace())
    generate_native = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(native_media_generation, "generate_native_media_response", generate_native)
    save = AsyncMock()
    monkeypatch.setattr(media_jobs, "_save_response", save)

    asyncio.run(media_jobs.execute_media_job(
        {"prompt": prompt, "media_type": media_type, "output_dir": str(tmp_path), **options},
        operation_id="job", store=store, worker_token="worker",
    ))

    if native:
        generate_native.assert_awaited_once()
        assert generate_native.await_args.kwargs["arguments"]["prompt"] == expected
        client.generate_content.assert_not_awaited()
    else:
        client.generate_content.assert_awaited_once()
        assert client.generate_content.await_args.kwargs["prompt"] == expected
        generate_native.assert_not_awaited()
    save.assert_awaited_once()


def test_stream_checkpoints_cid_then_full_identity_before_cleanup():
    from src.domain import CleanupObservation, CleanupState
    from src.services.media_generation import generate_media_response

    events = []

    class Client:
        async def generate_content_stream(self, **kwargs):
            yield SimpleNamespace(metadata=["c_one"], rcid="")
            yield SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one")

    class CheckpointStore:
        def checkpoint_media(self, operation_id, **kwargs):
            events.append(("checkpoint", kwargs))
            return True

    def schedule(response, **kwargs):
        events.append(("schedule", response.metadata))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_one")

    store = CheckpointStore()
    asyncio.run(generate_media_response(
        Client(), arguments={}, schedule_cleanup=schedule, retain_chat=False,
        delete_after_seconds=None, source="test",
        checkpoint=lambda response: media_jobs._checkpoint(store, "op", "token", response),
    ))
    assert [event[0] for event in events] == ["checkpoint", "schedule", "checkpoint"]
    assert events[0][1]["response_id"] is None
    assert events[2][1]["response_id"] == "r_one"
    assert events[2][1]["candidate_id"] == "rc_one"


def test_explicit_queued_media_keeps_guard_and_skips_download(monkeypatch):
    record = _record()
    response = SimpleNamespace(queued=True)
    save = AsyncMock()
    monkeypatch.setattr(media_jobs, "_save_generated_media", save)
    store = Store()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    save.assert_not_awaited()
    assert store.events[0][1]["state"] == "queued"
    assert all(event[0] != "release" for event in store.events)


def test_execute_timeout_after_checkpoint_keeps_operation_recoverable(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class Client:
        timeout = 30
        watchdog_timeout = 30

        async def generate_content_stream(self, **kwargs):
            yield SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one")
            raise TimeoutError()

    class ExecutingStore(Store):
        def configure_media(self, *args, **kwargs):
            self.events.append(("configured", kwargs))

        def checkpoint_media(self, operation_id, **kwargs):
            self.events.append(("checkpoint", kwargs))
            return True

    client = Client()
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k:
                        CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_one"))
    store = ExecutingStore()
    asyncio.run(media_jobs.execute_media_job(
        {"media_type": "image", "prompt": "draw", "model": "flash"},
        operation_id="op_one", store=store, worker_token="token",
    ))
    assert [event[0] for event in store.events] == ["configured", "checkpoint", "finish"]
    assert store.events[-1][1]["state"] == "partial"
    assert store.events[-1][1]["error_code"] == "TIMED_OUT"
    assert (client.timeout, client.watchdog_timeout) == (30, 30)


def test_execute_cancellation_after_checkpoint_does_not_release_chat(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    async def run():
        entered = asyncio.Event()

        class Client:
            timeout = 30
            watchdog_timeout = 30

            async def generate_content_stream(self, **kwargs):
                yield SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one")
                entered.set()
                await asyncio.Event().wait()

        class ExecutingStore(Store):
            def configure_media(self, *args, **kwargs):
                pass

            def checkpoint_media(self, operation_id, **kwargs):
                self.events.append(("checkpoint", kwargs))
                return True

        client = Client()
        monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
        monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
        monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k:
                            CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_one"))
        store = ExecutingStore()
        task = asyncio.create_task(media_jobs.execute_media_job(
            {"media_type": "image", "prompt": "draw", "model": "flash"},
            operation_id="op_one", store=store, worker_token="token",
        ))
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancellation must reach the worker")
        assert [event[0] for event in store.events] == ["checkpoint"]
        assert (client.timeout, client.watchdog_timeout) == (30, 30)

    asyncio.run(run())


def _real_store_setup(tmp_path):
    from src.constants import resolve_media_request
    from src.infrastructure.operation_store import OperationStore

    handle = "op_" + "a" * 32
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create(handle, "gemini_generate_media", requested_model="flash", thinking_level="standard")
    settings = resolve_media_request("flash", "image", "standard")
    destination = str((tmp_path / "images").resolve())
    assert store.configure_media(handle, "image", destination, handle, settings["request_model"], settings["backend_label"])
    assert store.claim_worker(handle, "first")
    return store, handle, destination


_SYNTHETIC_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAABCAIAAAB7QOjdAAAADElEQVR4nGP8zwACAAYIAQFazwZIAAAAAElFTkSuQmCC"
)


def _image_response(png=_SYNTHETIC_PNG):
    class Image:
        url = "https://example.invalid/private-signed-image"
        title = "generated response title"

        async def save(self, *, path, filename, verbose):
            destination = Path(path)
            destination.mkdir(parents=True, exist_ok=True)
            output = destination / filename
            output.write_bytes(png)
            return str(output)

    candidate = SimpleNamespace(rcid="rc_one", generated_images=[Image()], generated_videos=[], generated_media=[])
    return SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", candidates=[candidate], chosen=0,
                           images=[candidate.generated_images[0]], videos=[], media=[])


def test_ordered_references_and_saved_image_edit_keep_lineage_across_recovery(monkeypatch, tmp_path):
    from PIL import Image

    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState
    from src.infrastructure.operation_store import OperationStore
    from src.services import background
    from src.services.operations import OperationService

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite"))
    store = OperationStore()
    first, second = tmp_path / "foreground.png", tmp_path / "background.png"
    Image.new("RGB", (2, 1), "red").save(first)
    Image.new("RGB", (2, 1), "blue").save(second)
    parent_response = _image_response()
    child_response = _image_response(second.read_bytes())
    child_response.metadata = ["c_two", "r_two"]
    child_response.images[0].url = "https://example.invalid/child-image"
    queued = SimpleNamespace(metadata=child_response.metadata, rcid=child_response.rcid,
                             text="", queued=True, images=[], videos=[], media=[])
    uploads = []

    async def stream(**arguments):
        paths = [Path(path) for path in arguments["files"]]
        uploads.append((arguments["prompt"], paths, [path.read_bytes() for path in paths]))
        yield parent_response if len(uploads) == 1 else queued

    history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=child_response)])
    client = SimpleNamespace(generate_content_stream=stream, read_chat=AsyncMock(return_value=history))
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k:
                        CleanupObservation(state=CleanupState.PENDING))
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    launched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, _: launched.append(payload) or 123)

    async def finish_started_worker():
        envelope = launched[-1]
        handle, token = envelope["operation_id"], envelope["worker_token"]
        await media_jobs.execute_media_job(envelope["request"], operation_id=handle, store=store, worker_token=token)
        store.retire_worker(handle, token)

    async def run():
        started = await background.submit_media(
            {"media_type": "image", "prompt": "Combine foreground and background.",
             "image_paths": [str(first), str(second)]},
            operation="gemini_generate_image",
        )
        assert started.ok and started.data and started.data.operation_id
        parent_id = started.data.operation_id
        await finish_started_worker()
        assert uploads[0][1] == [first, second]
        assert uploads[0][2] == [first.read_bytes(), second.read_bytes()]
        reconnect = OperationService(OperationStore(), lambda: client, AsyncMock())
        parent = (await reconnect.get_result(parent_id)).data
        assert parent and parent.state.value == "completed" and parent.cleanup.state == CleanupState.COMPLETED
        assert len(parent.artifacts) == 1
        source = parent.artifacts[0]
        assert "image_pixels_decoded" in source.verification.methods

        request = {"media_type": "image", "prompt": "Update the foreground from the additional reference.",
                   "source_operation_id": parent_id, "image_paths": [str(first)]}
        child_id = "op_" + "c" * 32
        child = await background.submit_media(request, operation="gemini_edit_image", operation_id=child_id)
        assert child.ok and child.data and child.data.source_artifact_id == source.id
        assert child.data.source_operation_id == parent_id and child.data.operation_id == child_id
        assert "source_operation_id" not in launched[-1]["request"]
        await finish_started_worker()
        assert uploads[1][0] == (
            "Edit the first attached image using the other images as references: "
            "Update the foreground from the additional reference."
        )
        assert uploads[1][2] == [_SYNTHETIC_PNG, first.read_bytes()]
        assert uploads[1][1][1] == first and not uploads[1][1][0].exists()
        assert Path(source.local_path).read_bytes() == _SYNTHETIC_PNG

        pending = store.get(child_id)
        assert pending.state == "queued" and pending.cleanup_protected and pending.upstream_chat_id == "c_two"
        assert store.claim_worker(child_id, "new-worker")
        await media_jobs.recover_media_job(pending, store=OperationStore(), worker_token="new-worker")
        result = (await reconnect.get_result(child_id)).data
        assert result and result.state.value == "completed"
        assert result.source_operation_id == parent_id and result.source_artifact_id == source.id
        assert len(result.artifacts) == 1 and result.artifacts[0].id != source.id
        assert "image_pixels_decoded" in result.artifacts[0].verification.methods
        assert result.artifacts[0].observed_backend is None
        assert (await reconnect.get_status(child_id)).data.source_artifact_id == source.id
        client.read_chat.assert_awaited_once_with("c_two", limit=10)
        assert finalize.await_count == 2

        Path(source.local_path).unlink()
        replay = await background.submit_media(request, operation="gemini_edit_image", operation_id=child_id)
        assert replay.ok and replay.data.operation_id == child_id
        assert len(launched) == 2 and len(uploads) == 2
        assert str(first).encode() not in store.path.read_bytes()
        assert request["prompt"].encode() not in store.path.read_bytes()

    asyncio.run(run())


@pytest.mark.parametrize("invalid_source", [
    "other_account", "changed", "missing", "wrong_id", "ambiguous", "expired", "corrupt_saved_bytes",
])
def test_edit_rejects_unavailable_source_before_worker_dispatch(monkeypatch, tmp_path, invalid_source):
    from src.services import background

    store, parent_id, destination = _real_store_setup(tmp_path)
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(store.path))
    output = Path(destination)
    output.mkdir()
    source = output / "source.png"
    source.write_bytes(_SYNTHETIC_PNG if invalid_source != "corrupt_saved_bytes" else b"not an image")
    arts = [Artifact(id="saved", kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL,
                     mime_type="image/png", local_path=str(source))]
    if invalid_source == "ambiguous":
        second = output / "second.png"
        second.write_bytes(_SYNTHETIC_PNG)
        arts.append(Artifact(id="second", kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL,
                             mime_type="image/png", local_path=str(second)))
    assert store.save_media_artifacts(parent_id, tuple(arts), worker_token="first", artifact_state="local")
    if invalid_source == "other_account":
        monkeypatch.setenv("GEMINI_PSID", "other-synthetic-test-account")
    if invalid_source == "changed":
        source.write_bytes(b"\0" * len(_SYNTHETIC_PNG))
    if invalid_source == "missing":
        source.unlink()
    if invalid_source == "expired":
        import sqlite3
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE operations SET created_at=0, cleanup_protected=0")
    launch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", launch)
    result = asyncio.run(background.submit_media(
        {"media_type": "image", "prompt": "Edit the saved image.", "source_operation_id": parent_id,
         "source_artifact_id": "unknown" if invalid_source == "wrong_id" else None},
        operation="gemini_edit_image",
    ))
    assert not result.ok and result.meta.verification_status == "source_image_unavailable"
    launch.assert_not_awaited()


def test_worker_rejects_source_changed_after_acceptance(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper
    from src.services import background

    store, parent_id, destination = _real_store_setup(tmp_path)
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(store.path))
    output = Path(destination)
    output.mkdir()
    source = output / "stable.png"
    source.write_bytes(_SYNTHETIC_PNG)
    unavailable_other_output = output / "other.png"
    unavailable_other_output.write_bytes(_SYNTHETIC_PNG)
    images = tuple(Artifact(id=path.stem, kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL,
                            mime_type="image/png", local_path=str(path))
                   for path in (source, unavailable_other_output))
    assert store.save_media_artifacts(parent_id, images, worker_token="first", artifact_state="local")
    unavailable_other_output.unlink()
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, _: dispatched.append(payload) or 123)
    generate = AsyncMock()
    monkeypatch.setattr(media_jobs, "generate_media_response", generate)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: SimpleNamespace())
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())

    async def run():
        accepted = await background.submit_media(
            {"media_type": "image", "prompt": "Edit selected image.", "source_operation_id": parent_id,
             "source_artifact_id": source.stem},
            operation="gemini_edit_image",
        )
        assert accepted.ok and accepted.data.source_artifact_id == source.stem
        envelope = dispatched[0]
        source.write_bytes(b"x" * len(_SYNTHETIC_PNG))
        child_id = envelope["operation_id"]
        await media_jobs.execute_media_job(envelope["request"], operation_id=child_id,
                                           store=store, worker_token=envelope["worker_token"])
        stopped = store.get(child_id)
        assert stopped.state == "failed" and stopped.phase == "image_input_unavailable"
        assert stopped.upstream_chat_id is None and not stopped.cleanup_protected
        generate.assert_not_awaited()

    asyncio.run(run())


def test_execute_real_store_persists_local_then_default_cleanup(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    store, handle, destination = _real_store_setup(tmp_path)
    response = _image_response()
    events = []

    class Client:
        timeout = 30
        watchdog_timeout = 30

        async def generate_content_stream(self, **kwargs):
            yield response

    client = Client()
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *a, **k:
                        CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_one"))

    async def finalize(*args, **kwargs):
        record = store.get(handle)
        assert record is not None and record.state == "completed" and not record.cleanup_protected
        assert len(store.get_media_artifacts(handle)) == 1
        events.append(kwargs)
        return CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one")

    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    asyncio.run(media_jobs.execute_media_job(
        {"media_type": "image", "prompt": "private prompt", "model": "flash", "output_dir": destination},
        operation_id=handle, store=store, worker_token="first",
    ))
    record = store.get(handle)
    assert record is not None and record.state == "completed" and record.cleanup_state == "completed"
    assert record.output_dir == destination and record.filename == handle
    artifacts = store.get_media_artifacts(handle)
    assert len(artifacts) == 1 and artifacts[0]["mime_type"] == "image/png"
    assert artifacts[0]["local_path"].startswith(destination + "/")
    assert events[0]["retain_chat"] is False and events[0]["delete_after_seconds"] is None
    assert b"private prompt" not in store.path.read_bytes()
    assert b"private-signed-image" not in store.path.read_bytes()


@pytest.mark.parametrize("payload", [b"<html>Download unavailable</html>", _SYNTHETIC_PNG[:-20]],
                         ids=["html", "truncated_png"])
def test_unusable_generated_image_keeps_original_chat_protected(monkeypatch, tmp_path, payload):
    from pathlib import Path

    import src.client_wrapper as wrapper

    store, handle, destination = _real_store_setup(tmp_path)
    assert store.checkpoint_media(handle, worker_token="first", chat_id="c_one", response_id="r_one",
                                  candidate_id="rc_one")
    finalize = AsyncMock()
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)

    asyncio.run(media_jobs._save_response(
        object(), _image_response(payload), store.get(handle), store=store, token="first",
    ))
    record = store.get(handle)
    assert record.state == "partial" and record.error_code == "ARTIFACT_SAVE_FAILED"
    assert record.cleanup_protected and not store.get_media_artifacts(handle)
    assert not list(Path(destination).iterdir())
    finalize.assert_not_awaited()


def test_old_image_ledger_with_matching_hash_still_rejects_corrupt_bytes(tmp_path):
    from pathlib import Path

    from src.services.media_storage import read_verified_media_artifacts

    store, handle, destination = _real_store_setup(tmp_path)
    path = Path(destination) / "previously-saved.png"
    path.parent.mkdir()
    path.write_bytes(_SYNTHETIC_PNG[:-20])
    legacy = Artifact("image_legacy", ArtifactKind.IMAGE, ArtifactState.LOCAL,
                      local_path=str(path), mime_type="image/png")
    assert store.save_media_artifacts(handle, (legacy,), worker_token="first", artifact_state="local")
    with pytest.raises(ValueError, match="image_decode_failed"):
        read_verified_media_artifacts(store, store.get(handle))


def test_missing_image_decoder_fails_before_initializing_upstream(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper
    from src.services.media_storage import ImageDecoderUnavailable

    store, handle, destination = _real_store_setup(tmp_path)
    initialize = AsyncMock()
    monkeypatch.setattr(wrapper, "initialize_client", initialize)

    def no_decoder():
        raise ImageDecoderUnavailable("image_decoder_unavailable")

    monkeypatch.setattr(media_jobs, "require_image_decoder", no_decoder)
    asyncio.run(media_jobs.execute_media_job(
        {"media_type": "image", "prompt": "synthetic", "model": "flash", "output_dir": destination},
        operation_id=handle, store=store, worker_token="first",
    ))
    record = store.get(handle)
    assert record.state == "failed" and record.error_code == "CAPABILITY_UNAVAILABLE"
    assert record.verification_status == "image_decoder_unavailable"
    assert not record.upstream_chat_id and not record.cleanup_protected
    initialize.assert_not_awaited()


def test_real_store_crash_recovery_matches_only_exact_turn(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    store, handle, destination = _real_store_setup(tmp_path)
    assert store.checkpoint_media(handle, worker_token="first", chat_id="c_one", response_id="r_one", candidate_id="rc_one")
    claimed_at = store.clock()
    store.clock = lambda: claimed_at + 61
    assert store.claim_worker(handle, "recovery")
    assert not store.release_media_cleanup(handle, worker_token="first")
    response = _image_response()
    wrong = _image_response()
    wrong.metadata = ["c_one", "r_old"]
    wrong.candidates[0].generated_images = []
    history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=wrong),
                                     SimpleNamespace(role="model", model_output=response)])
    client = SimpleNamespace(read_chat=AsyncMock(return_value=history))
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    record = store.get(handle)
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="recovery"))
    record = store.get(handle)
    assert record is not None and record.state == "completed" and not record.cleanup_protected
    assert record.output_dir == destination and record.filename == handle
    assert len(store.get_media_artifacts(handle)) == 1
    client.read_chat.assert_awaited_once_with("c_one", limit=10)
    finalize.assert_awaited_once()


@pytest.mark.parametrize("rejection", [media_jobs._QUOTA_REJECTION, media_jobs._VIDEO_QUOTA_REJECTION])
def test_observed_limit_rejection_checkpoint_release_and_cleanup(monkeypatch, rejection):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            assert self.events[-1][1]["phase"] == "media_rejected"
            assert self.events[-1][1]["state"] == "partial"
            self.events.append(("release_rejected", operation_id))
            return True

    record = _record()
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=rejection,
                               images=[], videos=[], media=[])
    cleanup = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", cleanup)
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert [event[0] for event in store.events] == ["finish", "release_rejected", "cleanup", "finish"]
    assert store.events[-1][1]["state"] == "failed"
    assert store.events[-1][1]["error_code"] == "UPSTREAM_LIMIT_EXCEEDED"
    assert store.events[-1][1]["verification_status"] == "upstream_limit_message_observed"
    assert cleanup.await_args.kwargs["retain_chat"] is False


def test_recovery_recognizes_only_exact_matching_candidate_rejection(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    response = _response()
    response.candidates[0].text = media_jobs._QUOTA_REJECTION
    response.candidates[0].generated_images = []
    history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=response)])
    _prepare(monkeypatch, history)
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one")))
    store = RejectionStore()
    asyncio.run(media_jobs.recover_media_job(_record(), store=store, worker_token="worker"))
    assert store.events[-1][1]["state"] == "failed"
    assert store.events[-1][1]["error_code"] == "UPSTREAM_LIMIT_EXCEEDED"


def test_other_limit_text_never_releases_protection(monkeypatch):
    for text in ("usage limit reached", media_jobs._QUOTA_REJECTION + " Please wait.", ""):
        response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=text,
                                   images=[], videos=[], media=[])
        monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(
            return_value=SimpleNamespace(artifacts=(), failures=())))
        store = Store()
        asyncio.run(media_jobs._save_response(None, response, _record(), store=store, token="worker"))
        assert [event[0] for event in store.events] == ["finish"]
        assert store.events[0][1]["state"] == "partial"
        assert store.events[0][1]["error_code"] is None


@pytest.mark.parametrize("code,verification", [
    ("UPSTREAM_LIMIT_EXCEEDED", "upstream_limit_message_observed"),
    ("ARTIFACT_NOT_RETURNED", "model_media_refusal_observed"),
    ("UPSTREAM_REJECTED", "upstream_media_error_card_observed"),
])
def test_real_store_resumes_rejection_cleanup_after_crash(monkeypatch, tmp_path, code, verification):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    store, handle, _ = _real_store_setup(tmp_path)
    assert store.checkpoint_media(handle, worker_token="first", chat_id="c_one", response_id="r_one", candidate_id="rc_one")
    assert store.finish_worker(handle, worker_token="first", state="partial", phase="media_rejected",
                               verification_status=verification, error_code=code)
    claimed_at = store.clock()
    store.clock = lambda: claimed_at + 61
    assert store.claim_worker(handle, "recovery")
    assert not store.release_failed_media_cleanup(handle, worker_token="first")
    client = SimpleNamespace(read_chat=AsyncMock(side_effect=AssertionError("must not read deleted chat")))
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    asyncio.run(media_jobs.recover_media_job(store.get(handle), store=store, worker_token="recovery"))
    record = store.get(handle)
    assert record is not None and record.state == "failed" and not record.cleanup_protected
    assert record.error_code == code and record.cleanup_state == "completed"
    client.read_chat.assert_not_awaited()
    finalize.assert_awaited_once()


def test_music_refusal_requires_exact_prefix_and_no_media_cards(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one")))
    save = AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=()))
    monkeypatch.setattr(media_jobs, "_save_generated_media", save)
    record = _record()
    record.media_type = "music"
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one",
                               text=media_jobs._MUSIC_REFUSAL + " Here are composition ideas.",
                               images=[], videos=[], media=[])
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(return_value=[]))
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert store.events[-1][1]["state"] == "failed"
    assert store.events[-1][1]["error_code"] == "ARTIFACT_NOT_RETURNED"
    assert store.events[-1][1]["verification_status"] == "model_media_refusal_observed"
    save.assert_not_awaited()

    # Similar prose, a different modality, and a recovered card are not proof of refusal.
    for text, kind, cards in ((media_jobs._MUSIC_REFUSAL.replace("directly", "currently"), "music", []),
                              (media_jobs._MUSIC_REFUSAL, "video", []),
                              (media_jobs._MUSIC_REFUSAL, "music", [object()])):
        response.text = text
        record.media_type = kind
        monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(return_value=cards))
        store = RejectionStore()
        asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
        assert [event[0] for event in store.events] == ["finish"]
        assert store.events[0][1]["state"] == "partial"


def test_music_refusal_with_structured_artifact_or_failed_card_lookup_stays_unverified(monkeypatch):
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=media_jobs._MUSIC_REFUSAL,
                               images=[], videos=[], media=[object()])
    record = _record()
    record.media_type = "music"
    save = AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=()))
    monkeypatch.setattr(media_jobs, "_save_generated_media", save)
    store = Store()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    save.assert_awaited_once()
    assert store.events[0][1]["state"] == "partial"

    response.media = []
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(side_effect=RuntimeError()))
    store = Store()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert store.events[0][1]["state"] == "partial"
    assert store.events[0][1]["phase"] == "media_readback_error"


def test_chinese_music_quota_requires_full_prefix_and_empty_cards(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one")))
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(
        return_value=SimpleNamespace(artifacts=(), failures=())))
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", images=[], videos=[], media=[],
                               text=media_jobs._MUSIC_QUOTA_PREFIX + " 测试曲风。")
    record = _record()
    record.media_type = "music"
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(return_value=[]))
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert store.events[-1][1]["error_code"] == "UPSTREAM_LIMIT_EXCEEDED"
    assert store.events[-1][1]["state"] == "failed"
    for text, modality, cards in ((media_jobs._MUSIC_QUOTA_PREFIX[:-1], "music", []),
                                  (media_jobs._MUSIC_QUOTA_PREFIX, "image", []),
                                  (media_jobs._MUSIC_QUOTA_PREFIX, "music", [object()])):
        response.text = text
        record.media_type = modality
        monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(return_value=cards))
        store = RejectionStore()
        asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
        assert store.events[0][1]["state"] == "partial"
        assert all(event[0] != "release_rejected" for event in store.events)


def test_readback_error_and_incomplete_identity_have_distinct_safe_phases(monkeypatch):
    record = _record()
    record.upstream_candidate_id = None
    store = Store()
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="worker"))
    assert store.events[0][1]["phase"] == "media_identity_incomplete"
    record = _record()
    client = _prepare(monkeypatch, None)
    client.read_chat.side_effect = RuntimeError("synthetic private detail")
    store = Store()
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="worker"))
    assert store.events[0][1]["phase"] == "media_readback_error"
    assert store.events[0][1]["error_code"] == "READBACK_UNVERIFIED"
    assert "synthetic private detail" not in str(store.events)


def test_recovery_never_reads_or_finalizes_under_another_account(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper

    monkeypatch.setenv("GEMINI_PSID", "synthetic-original-account")
    store, handle, _ = _real_store_setup(tmp_path)
    assert store.checkpoint_media(handle, worker_token="first", chat_id="c_one", response_id="r_one", candidate_id="rc_one")
    monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    client = SimpleNamespace(read_chat=AsyncMock())
    provider = AsyncMock()
    monkeypatch.setattr(wrapper, "get_gemini_client", provider)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    finalize = AsyncMock()
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    asyncio.run(media_jobs.recover_media_job(store.get(handle), store=store, worker_token="first"))
    provider.assert_not_called()
    client.read_chat.assert_not_awaited()
    finalize.assert_not_awaited()
    assert store.get(handle).cleanup_protected


@pytest.mark.parametrize("phase", ["initialize", "read_chat", "music_cards"])
def test_media_recovery_phase_timeout_keeps_guard_and_handle(monkeypatch, phase):
    import src.client_wrapper as wrapper

    async def run():
        monkeypatch.setattr(media_jobs, "MEDIA_READBACK_TIMEOUT_SECONDS", 0.01)
        entered = asyncio.Event()

        async def hang(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        response = _response()
        history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=response)])
        client = _prepare(monkeypatch, history)
        if phase == "initialize":
            monkeypatch.setattr(wrapper, "initialize_client", hang)
        elif phase == "read_chat":
            client.read_chat = AsyncMock(side_effect=hang)
        else:
            monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", hang)
        record = _record()
        if phase == "music_cards":
            record.media_type = "music"
        store = Store()
        save = AsyncMock()
        monkeypatch.setattr(media_jobs, "_save_generated_media", save)
        await asyncio.wait_for(media_jobs.recover_media_job(record, store=store, worker_token="worker"), timeout=0.5)
        assert entered.is_set()
        assert len(store.events) == 1 and store.events[0][0] == "finish"
        expected = {"initialize": "media_initialize_timeout", "read_chat": "media_readback_timeout",
                    "music_cards": "media_music_readback_timeout"}
        assert store.events[0][1]["phase"] == expected[phase]
        assert store.events[0][1]["state"] == "partial" and store.events[0][1]["error_code"] == "TIMED_OUT"
        save.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["read_chat", "music_cards"])
def test_media_late_readback_ignoring_cancel_cannot_complete_or_release(monkeypatch, phase):
    async def run():
        monkeypatch.setattr(media_jobs, "MEDIA_READBACK_TIMEOUT_SECONDS", 0.01)
        release, finished = asyncio.Event(), asyncio.Event()
        response = _response()
        history = SimpleNamespace(turns=[SimpleNamespace(role="model", model_output=response)])
        client = _prepare(monkeypatch, history)

        async def late(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            finished.set()
            return history if phase == "read_chat" else [object()]

        if phase == "read_chat":
            client.read_chat = AsyncMock(side_effect=late)
        else:
            monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", late)
        save = AsyncMock()
        monkeypatch.setattr(media_jobs, "_save_generated_media", save)
        record = _record()
        if phase == "music_cards":
            record.media_type = "music"
        store = Store()
        await asyncio.wait_for(media_jobs.recover_media_job(record, store=store, worker_token="worker"), timeout=0.5)
        assert store.events[0][1]["state"] == "partial"
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.5)
        await asyncio.sleep(0)
        assert [event[0] for event in store.events] == ["finish"]
        save.assert_not_awaited()

    asyncio.run(run())


def test_real_store_readback_timeout_preserves_cid_lease_and_protection(monkeypatch, tmp_path):
    store, handle, _ = _real_store_setup(tmp_path)
    assert store.checkpoint_media(handle, worker_token="first", chat_id="c_one", response_id="r_one", candidate_id="rc_one")
    client = _prepare(monkeypatch, None)
    async def hang(*args, **kwargs):
        await asyncio.Event().wait()
    client.read_chat = AsyncMock(side_effect=hang)
    monkeypatch.setattr(media_jobs, "MEDIA_READBACK_TIMEOUT_SECONDS", 0.01)
    asyncio.run(media_jobs.recover_media_job(store.get(handle), store=store, worker_token="first"))
    record = store.get(handle)
    assert record.state == "partial" and record.phase == "media_readback_timeout"
    assert record.upstream_chat_id == "c_one" and record.worker_token == "first" and record.cleanup_protected
    assert store.get_media_artifacts(handle) == ()


@pytest.mark.parametrize("transition", ["account", "lease"])
def test_early_media_cid_is_preserved_without_wrong_account_cleanup(monkeypatch, tmp_path, transition):
    import src.client_wrapper as wrapper
    from src.services.cleanup_scope import observe_upstream_identity

    store, handle, destination = _real_store_setup(tmp_path)
    captured_at = store.clock()
    scheduled = []

    class Client:
        timeout = 30
        watchdog_timeout = 30
        requests = 0
        def create_cleanup_chat(self):
            return SimpleNamespace(cid="", metadata=[])
        async def generate_content_stream(self, **kwargs):
            self.requests += 1
            chat = kwargs["chat"]
            if transition == "account":
                monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
            else:
                store.clock = lambda: captured_at + 61
                assert store.claim_worker(handle, "replacement")
            chat.cid = "c_early_media"
            chat.metadata = ["c_early_media", "r_early_media"]
            observe_upstream_identity(chat)
            yield  # pragma: no cover

    client = Client()
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", lambda *args, **kwargs: scheduled.append(args))
    asyncio.run(media_jobs.execute_media_job(
        {"media_type": "image", "prompt": "synthetic", "model": "flash", "output_dir": destination},
        operation_id=handle, store=store, worker_token="first",
    ))
    record = store.get(handle)
    assert client.requests == 1 and not scheduled
    assert record.upstream_chat_id == "c_early_media" and record.cleanup_protected
    if transition == "lease":
        assert record.worker_token == "replacement"


def test_media_timeout_before_first_yield_keeps_observed_cid_and_guard(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState
    from src.services.cleanup_scope import observe_upstream_identity

    store, handle, destination = _real_store_setup(tmp_path)
    scheduled = []

    class Client:
        timeout = 30
        watchdog_timeout = 30
        requests = 0
        def create_cleanup_chat(self):
            return SimpleNamespace(cid="", metadata=[])
        async def generate_content_stream(self, **kwargs):
            self.requests += 1
            chat = kwargs["chat"]
            chat.cid = "c_first_pending"
            chat.metadata = ["c_first_pending", "r_first_pending"]
            observe_upstream_identity(chat)
            await asyncio.Event().wait()
            yield  # pragma: no cover

    client = Client()
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())
    def schedule(response, **kwargs):
        scheduled.append((response.cid, kwargs))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=response.cid)
    monkeypatch.setattr(wrapper, "schedule_remote_chat_cleanup_from_response", schedule)
    monkeypatch.setattr(media_jobs, "media_timeout", lambda *_args: 0.01)
    asyncio.run(media_jobs.execute_media_job(
        {"media_type": "image", "prompt": "synthetic", "model": "flash", "output_dir": destination},
        operation_id=handle, store=store, worker_token="first",
    ))
    record = store.get(handle)
    assert record.state == "partial" and record.error_code == "TIMED_OUT" and record.cleanup_protected
    assert record.upstream_chat_id == "c_first_pending" and record.upstream_response_id == "r_first_pending"
    assert client.requests == 1 and scheduled[0][0] == "c_first_pending"
    assert scheduled[0][1]["delete_after_seconds"] >= 1800


def test_initial_music_result_also_bounds_card_readback(monkeypatch):
    """The first response needs the same bound as a later recovery attempt."""
    from src.services.deadlines import await_before_deadline

    record = _record()
    record.media_type = "music"
    response = _response()
    response.rcid = "rc_one"
    store = Store()
    budgets = []

    async def blocked(*_args, **_kwargs):
        await asyncio.Event().wait()

    async def short_deadline(awaitable, *, timeout):
        budgets.append(timeout)
        return await await_before_deadline(awaitable, timeout=0.01)

    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", blocked)
    monkeypatch.setattr(media_jobs, "await_before_deadline", short_deadline)
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert budgets == [media_jobs.MEDIA_READBACK_TIMEOUT_SECONDS]
    assert store.events[-1][1]["phase"] == "media_music_readback_timeout"
    assert store.events[-1][1]["error_code"] == "TIMED_OUT"
    assert not any(event[0] in {"saved", "release", "cleanup"} for event in store.events)


def test_video_refusal_is_exact_and_requires_no_artifacts(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=())))
    record = _record()
    record.media_type = "video"
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=media_jobs._VIDEO_REFUSAL,
                               images=[], videos=[], media=[])
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert store.events[-1][1]["state"] == "failed" and store.events[-1][1]["error_code"] == "ARTIFACT_NOT_RETURNED"
    assert store.events[-1][1]["verification_status"] == "model_media_refusal_observed"
    assert finalize.await_args.kwargs["retain_chat"] is False
    for text, kind, images, queued in ((media_jobs._VIDEO_REFUSAL + "后续内容", "video", [], False),
                                       (media_jobs._VIDEO_REFUSAL, "image", [], False),
                                       (media_jobs._VIDEO_REFUSAL, "video", [object()], False),
                                       (media_jobs._VIDEO_REFUSAL, "video", [], True)):
        response.text, response.images, response.queued = text, images, queued
        record.media_type = kind
        store = RejectionStore()
        asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
        assert all(event[0] != "release_rejected" for event in store.events)
        assert store.events[0][1]["state"] in {"partial", "queued"}


@pytest.mark.parametrize("failure_reply", [
    GEMINI_GENERIC_FAILURE_REPLY,
    "Sorry, something went wrong. Please try your request again.",
    "我是一个语言模型，这超出了我的设计用途。",
])
@pytest.mark.parametrize("completed, same_candidate, same_reply, has_media", [
    (True, True, True, False),
    (False, True, True, False),
    (True, False, True, False),
    (True, True, False, False),
    (True, True, True, True),
])
def test_image_generic_failure_releases_cleanup_only_after_exact_readback(
    monkeypatch, completed, same_candidate, same_reply, has_media, failure_reply,
):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState
    from src.infrastructure.rpc_parsers import RPCParseResult

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=())))
    other_failure_reply = ("Sorry, something went wrong. Please try your request again."
                           if failure_reply == GEMINI_GENERIC_FAILURE_REPLY else GEMINI_GENERIC_FAILURE_REPLY)
    read_turn = AsyncMock(return_value={
        "chat_id": "c_one", "rid": "r_one", "rcid": "rc_one" if same_candidate else "rc_other",
        "completed": completed, "text": failure_reply if same_reply else other_failure_reply,
    })
    monkeypatch.setattr(media_jobs, "fetch_workflow_turn", read_turn)
    strict_notice = AsyncMock(return_value=RPCParseResult(
        "success" if completed and same_candidate and same_reply else "changed_shape",
    ))
    monkeypatch.setattr(media_jobs, "read_completed_empty_media_turn", strict_notice)
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=failure_reply,
                               images=[object()] if has_media else [], videos=[], media=[])
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, _record(), store=store, token="worker"))
    if completed and same_candidate and same_reply and not has_media:
        assert store.events[-1][1]["state"] == "failed"
        assert store.events[-1][1]["error_code"] == "UPSTREAM_REJECTED"
        assert store.events[-1][1]["verification_status"] == "upstream_media_failure_reply_observed"
        assert ("release_rejected", "op_one") in store.events
        finalize.assert_awaited_once()
    else:
        assert store.events[-1][1]["state"] == "partial"
        finalize.assert_not_awaited()
    if has_media:
        read_turn.assert_not_awaited()
        strict_notice.assert_not_awaited()
    elif failure_reply == media_jobs._IMAGE_CHAT_REFUSAL:
        assert read_turn.await_args.args == (None, "c_one", "r_one", "rc_one")
        strict_notice.assert_not_awaited()
    else:
        read_turn.assert_not_awaited()
        strict_notice.assert_awaited_once_with(None, "c_one", "r_one", "rc_one", failure_reply)


def test_abc_music_refusal_requires_prefix_and_verified_empty_cards(monkeypatch):
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one"))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", finalize)
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(return_value=SimpleNamespace(artifacts=(), failures=())))
    record = _record()
    record.media_type = "music"
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=media_jobs._MUSIC_REFUSAL_ABC + "a song.",
                               images=[], videos=[], media=[])
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(return_value=[]))
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert store.events[-1][1]["state"] == "failed" and store.events[-1][1]["error_code"] == "ARTIFACT_NOT_RETURNED"
    assert store.events[-1][1]["verification_status"] == "model_media_refusal_observed"
    assert finalize.await_args.kwargs["retain_chat"] is False
    for text, kind, cards, queued in ((media_jobs._MUSIC_REFUSAL_ABC[:-3], "music", [], False),
                                      ("Quoted: " + media_jobs._MUSIC_REFUSAL_ABC, "music", [], False),
                                      (media_jobs._MUSIC_REFUSAL_ABC, "video", [], False),
                                      (media_jobs._MUSIC_REFUSAL_ABC, "music", [object()], False),
                                      (media_jobs._MUSIC_REFUSAL_ABC, "music", [], True)):
        response.text, response.queued = text, queued
        record.media_type = kind
        monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(return_value=cards))
        store = RejectionStore()
        asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
        assert all(event[0] != "release_rejected" for event in store.events)
        assert store.events[0][1]["state"] in {"partial", "queued"}
    response.text, response.queued, record.media_type = media_jobs._MUSIC_REFUSAL_ABC, False, "music"
    monkeypatch.setattr(media_jobs, "fetch_music_media_from_chat", AsyncMock(side_effect=RuntimeError("unverified")))
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(None, response, record, store=store, token="worker"))
    assert store.events[0][1]["phase"] == "media_readback_error"
    assert all(event[0] != "release_rejected" for event in store.events)


@pytest.mark.parametrize("complete", [True, False])
def test_abc_refusal_uses_real_strict_raw_card_evidence(monkeypatch, complete):
    import json
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState
    from src.infrastructure.rpc_contracts import get_contract

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    candidate = ["rc_one"] + [None] * 12
    candidate[8] = [2 if complete else 1]
    candidate[12] = [{"87": []}]
    body = [[[[None, "r_one"], None, None, [[candidate]]]]]
    rpc_id = get_contract("media.music_chat").rpc_id
    wire = json.dumps([["wrb.fr", rpc_id, json.dumps(body)]])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=SimpleNamespace(text=wire)))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one")))
    record = _record()
    record.media_type = "music"
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one", text=media_jobs._MUSIC_REFUSAL_ABC + "music",
                               images=[], videos=[], media=[])
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(client, response, record, store=store, token="worker"))
    assert store.events[-1][1]["state"] == ("failed" if complete else "partial")
    assert any(event[0] == "release_rejected" for event in store.events) is complete
    if not complete:
        assert store.events[-1][1]["phase"] == "media_readback_error"


@pytest.mark.parametrize("case", ["match", "notice", "queued", "asset", "other_turn", "other_candidate",
                                  "incomplete", "malformed", "other_card"])
def test_music_error_card_requires_notice_and_verified_exact_turn(monkeypatch, case):
    import json
    import src.client_wrapper as wrapper
    from src.domain import CleanupObservation, CleanupState
    from src.infrastructure.rpc_contracts import get_contract

    class RejectionStore(Store):
        def release_failed_media_cleanup(self, operation_id, **kwargs):
            self.events.append(("release_rejected", operation_id))
            return True

    candidate = ["rc_one"] + [None] * 12
    candidate[8] = [1 if case == "incomplete" else 2]
    candidate[12] = [{"8": [], "73": [None, "Generating your track", None, 3]}]
    if case == "malformed":
        candidate[12][0]["73"][3] = 4
    if case == "other_candidate":
        candidate[0] = "rc_other"
    if case == "other_card":
        candidate[12][0]["87"] = [["asset"]]
    rid = "r_other" if case == "other_turn" else "r_one"
    body = [[[[None, rid], None, None, [[candidate]]]]]
    rpc_id = get_contract("media.music_chat").rpc_id
    wire = json.dumps([["wrb.fr", rpc_id, json.dumps(body)]])
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=SimpleNamespace(text=wire)))
    monkeypatch.setattr(wrapper, "finalize_remote_chat_cleanup_from_response", AsyncMock(
        return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_one")))
    record = _record()
    record.media_type = "music"
    response = SimpleNamespace(metadata=["c_one", "r_one"], rcid="rc_one",
                               text=media_jobs._MUSIC_ERROR_NOTICE + (" extra" if case == "notice" else ""),
                               queued=case == "queued", images=[object()] if case == "asset" else [],
                               videos=[], media=[])
    store = RejectionStore()
    asyncio.run(media_jobs._save_response(client, response, record, store=store, token="worker"))
    if case == "match":
        assert [event[0] for event in store.events] == ["finish", "release_rejected", "cleanup", "finish"]
        assert store.events[0][1]["state"] == "partial"
        assert store.events[-1][1]["error_code"] == "UPSTREAM_REJECTED"
        assert store.events[-1][1]["verification_status"] == "upstream_media_error_card_observed"
    else:
        assert all(event[0] != "release_rejected" for event in store.events)


def test_initial_media_initialization_deadline_does_not_start_generation_after_late_completion(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper

    async def run():
        store, handle, destination = _real_store_setup(tmp_path)
        assert store.checkpoint_media(handle, worker_token="first", chat_id="c_known",
                                      response_id="r_known", candidate_id="rc_known")
        release, finished = asyncio.Event(), asyncio.Event()

        async def late_initialization():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
            finished.set()

        client = SimpleNamespace(generate_content_stream=AsyncMock())
        monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
        monkeypatch.setattr(wrapper, "initialize_client", late_initialization)
        monkeypatch.setattr(media_jobs, "MEDIA_READBACK_TIMEOUT_SECONDS", 0.01)
        generate = AsyncMock()
        monkeypatch.setattr(media_jobs, "generate_media_response", generate)
        request = {"media_type": "image", "prompt": "synthetic", "model": "flash", "output_dir": destination}
        await asyncio.wait_for(media_jobs.execute_media_job(request, operation_id=handle, store=store,
                                                          worker_token="first"), timeout=0.5)
        record = store.get(handle)
        assert record.state == "partial" and record.phase == "media_initialize_timeout"
        assert record.error_code == "TIMED_OUT" and record.upstream_chat_id == "c_known"
        assert record.upstream_response_id == "r_known" and record.upstream_candidate_id == "rc_known"
        assert record.worker_token == "first" and record.cleanup_protected
        generate.assert_not_awaited()
        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.5)
        await asyncio.sleep(0)
        generate.assert_not_awaited()
        assert store.get(handle) == record

    asyncio.run(run())


def test_background_music_controls_survive_dispatch_and_worker_without_entering_generation_arguments(monkeypatch, tmp_path):
    import src.client_wrapper as wrapper
    from src.services import background

    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "music.sqlite"))
    monkeypatch.delenv("GEMINI_WEB_MEDIA_TRANSPORT", raising=False)
    captured = []
    generated = []

    async def launch(store, record, kind, request=None):
        captured.append((store, record, request))
        return background._accepted(record)

    monkeypatch.setattr(background, "_dispatch", launch)
    result = asyncio.run(background.submit_media({"media_type": "music", "prompt": "synthetic instrumental",
        "model": "neon", "music_duration": "short", "music_vocals": "instrumental"}, operation="gemini_create_media"))
    assert result.ok and len(captured) == 1
    store, record, request = captured[0]
    assert request["music_duration"] == "short" and request["music_vocals"] == "instrumental"
    assert store.claim_worker(record.operation_id, "music-worker")
    client = SimpleNamespace(timeout=30, watchdog_timeout=30)
    monkeypatch.setattr(wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(wrapper, "initialize_client", AsyncMock())

    async def generate(client, **kwargs):
        generated.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(media_jobs, "generate_media_response", generate)
    save = AsyncMock()
    monkeypatch.setattr(media_jobs, "_save_response", save)
    asyncio.run(media_jobs.execute_media_job(request, operation_id=record.operation_id, store=store, worker_token="music-worker"))
    assert len(generated) == 1
    assert generated[0]["music_duration"] == "short" and generated[0]["music_vocals"] == "instrumental"
    assert "music_duration" not in generated[0]["arguments"] and "music_vocals" not in generated[0]["arguments"]
    save.assert_awaited_once()


@pytest.mark.parametrize("media_type,options", [("music", {"music_duration": "unknown"}),
    ("music", {"music_vocals": "unknown"}), ("video", {"music_duration": "short"}),
    ("image", {"music_vocals": "auto"})])
def test_background_invalid_music_controls_reject_before_worker_dispatch(monkeypatch, media_type, options):
    from src.services import background

    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    result = asyncio.run(background.submit_media({"media_type": media_type, "prompt": "synthetic", **options},
                                               operation="gemini_create_media"))
    assert not result.ok and result.error.code.value == "INVALID_ARGUMENT"
    dispatch.assert_not_awaited()


def test_background_explicit_sdk_music_controls_reject_before_worker_dispatch(monkeypatch):
    from src.services import background

    monkeypatch.setenv("GEMINI_WEB_MEDIA_TRANSPORT", "sdk")
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    result = asyncio.run(background.submit_media({"media_type": "music", "prompt": "synthetic",
        "music_duration": "short", "music_vocals": "instrumental"}, operation="gemini_create_media"))
    assert not result.ok and result.error.code.value == "CAPABILITY_UNAVAILABLE"
    dispatch.assert_not_awaited()


def test_worker_invalid_music_controls_reject_before_client_initialization(monkeypatch):
    import src.client_wrapper as wrapper

    initialized = AsyncMock()
    monkeypatch.setattr(wrapper, "initialize_client", initialized)
    store = Store()
    asyncio.run(media_jobs.execute_media_job({"media_type": "video", "prompt": "synthetic", "music_vocals": "auto"},
        operation_id="op_synthetic", store=store, worker_token="worker"))
    assert store.events[0][1]["phase"] == "input_rejected"
    initialized.assert_not_awaited()


class _ReadRateLimited(RuntimeError):
    code = 'RATE_LIMITED'


@pytest.mark.parametrize('phase', ['initialize', 'legacy_history', 'scoped_history'])
def test_media_recovery_preserves_rate_limit_and_never_resubmits(monkeypatch, phase):
    import src.client_wrapper as wrapper

    client = _prepare(monkeypatch, None)
    limited = AsyncMock(side_effect=_ReadRateLimited('Synthetic upstream HTTP 429'))
    if phase == 'initialize':
        monkeypatch.setattr(wrapper, 'initialize_client', limited)
    elif phase == 'legacy_history':
        client.read_chat = limited
    else:
        client.read_chat_for_media = limited
    generate = AsyncMock()
    save = AsyncMock()
    monkeypatch.setattr(media_jobs, 'generate_media_response', generate)
    monkeypatch.setattr(media_jobs, '_save_generated_media', save)
    store = Store()
    asyncio.run(media_jobs.recover_media_job(_record(), store=store, worker_token='worker'))
    assert len(store.events) == 1 and store.events[0][0] == 'finish'
    data = store.events[0][1]
    assert data['state'] == 'partial' and data['error_code'] == 'RATE_LIMITED'
    assert data['phase'] == 'media_readback_rate_limited'
    limited.assert_awaited_once()
    if phase == 'scoped_history':
        client.read_chat.assert_not_awaited()
    generate.assert_not_awaited()
    save.assert_not_awaited()


@pytest.mark.parametrize('phase', ['video_notice', 'legacy_notice', 'music_cards', 'music_proof'])
@pytest.mark.parametrize('ownership', ['same', 'account_changed', 'lease_lost'])
def test_media_proof_throttling_keeps_identity_and_cleanup_guard(monkeypatch, phase, ownership):
    record = _record()
    record.media_type = 'video' if phase in {'video_notice', 'legacy_notice'} else 'music'
    text = {'video_notice': 'I seem to be encountering an error.',
            'legacy_notice': GEMINI_GENERIC_FAILURE_REPLY,
            'music_cards': 'Synthetic music result',
            'music_proof': "I can't generate or output an audio file directly right now, as the music generation tool isn't available in this session."}[phase]
    response = SimpleNamespace(metadata=[record.upstream_chat_id, record.upstream_response_id],
        rcid=record.upstream_candidate_id, text=text, images=[], videos=[], media=[])
    store = Store()
    active = [False]

    @contextmanager
    def read_scope():
        assert not active[0]
        active[0] = True
        try:
            yield
        finally:
            active[0] = False

    async def blocked(*args, **kwargs):
        assert active[0]
        if ownership == 'account_changed':
            monkeypatch.setenv('GEMINI_PSID', 'different-offline-owner')
        elif ownership == 'lease_lost':
            store.owner = False
        raise _ReadRateLimited('Synthetic batch status code 429')

    callback = AsyncMock(side_effect=blocked)
    if phase == 'music_cards':
        monkeypatch.setattr(media_jobs, 'fetch_music_media_from_chat', callback)
    else:
        monkeypatch.setattr(media_jobs, 'read_completed_empty_media_turn', callback)
        if phase == 'music_proof':
            monkeypatch.setattr(media_jobs, 'fetch_music_media_from_chat', AsyncMock(return_value=[]))
    save = AsyncMock()
    reject = AsyncMock()
    monkeypatch.setattr(media_jobs, '_save_generated_media', save)
    monkeypatch.setattr(media_jobs, '_finish_media_rejection', reject)
    asyncio.run(media_jobs._save_response(SimpleNamespace(media_readback_scope=read_scope), response, record,
                                         store=store, token='worker'))
    callback.assert_awaited_once()
    assert not active[0]
    save.assert_not_awaited()
    reject.assert_not_awaited()
    assert record.upstream_chat_id == 'c_one' and record.upstream_response_id == 'r_one'
    assert record.upstream_candidate_id == 'rc_one'
    if ownership == 'same':
        assert len(store.events) == 1 and store.events[0][0] == 'finish'
        assert store.events[0][1]['error_code'] == 'RATE_LIMITED'
        assert store.events[0][1]['state'] == 'partial'
    else:
        assert not store.events


def test_rate_limited_native_cid_only_recovery_retains_provenance(monkeypatch, tmp_path):
    from src.infrastructure.operation_store import OperationStore

    client = _prepare(monkeypatch, None)
    store = OperationStore(tmp_path / 'rate.sqlite')
    store.create_or_get('op_rate', 'gemini_generate_video', media_type='video',
                        request_model='gemini-neon', effective_backend='Gemini Web native browser')
    assert store.claim_worker('op_rate', 'worker')
    media_jobs._checkpoint(store, 'op_rate', 'worker', SimpleNamespace(
        metadata=['c_0123456789abcdef'], rcid='', _mcp_native_media=True))
    record = store.get('op_rate')
    limited = AsyncMock(side_effect=_ReadRateLimited('Synthetic HTTP 429'))
    monkeypatch.setattr(media_jobs, 'read_native_media_response', limited)
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token='worker'))
    after = store.get('op_rate')
    assert after.state == 'partial' and after.error_code == 'RATE_LIMITED'
    assert after.phase == 'native_media_submitted' and after.cleanup_protected
    assert after.upstream_chat_id == record.upstream_chat_id
    assert after.upstream_response_id is None and after.upstream_candidate_id is None
    limited.assert_awaited_once()
    client.read_chat.assert_not_awaited()
