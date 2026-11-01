"""Recovery never regenerates or credits a different turn or candidate."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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


def _image_response():
    import base64
    from pathlib import Path

    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aWQAAAAASUVORK5CYII=")

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
