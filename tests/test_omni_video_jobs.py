"""Exercise the exact video interaction and its local result across restarts."""

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2
import pytest

from src.constants import OMNI_VIDEO_MODEL, resolve_media_request
from src.domain import ArtifactKind, CleanupState, DomainErrorCode, OperationState
from src.infrastructure.operation_store import OperationStore
from src.operation_worker import run_worker
from src.services import background, media_storage
from src.services.operations import OperationService

PROMPT = "Synthetic scene of a droplet"
KEY = "synthetic-local-omni-key"
INTERACTION = "v1_synthetic_video"
HANDLE = "op_" + "7" * 32
MP4_FIXTURE = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00fixture-video"


@pytest.fixture
def jobs(monkeypatch):
    # These tests use only MockTransport, with real operation persistence.
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 9)
    monkeypatch.setattr(media_storage, "probe_playable_av_tracks", lambda path: {"video"})
    from src import client_wrapper
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: pytest.fail("API job initialized Web"))
    return OperationStore(), dispatched


def _mock_http(monkeypatch, handler):
    client_class = httpx2.AsyncClient
    monkeypatch.setattr(httpx2, "AsyncClient", lambda **kwargs: client_class(
        transport=httpx2.MockTransport(handler), **kwargs,
    ))


async def _submit(**extra):
    return await background.submit_media({
        "prompt": PROMPT, "model": OMNI_VIDEO_MODEL, "media_type": "video", "duration_seconds": 15, **extra,
    }, operation="gemini_generate_video", operation_id=HANDLE)


def _completed(data: bytes | None = MP4_FIXTURE):
    output = ({"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(data).decode()}
              if data is not None else {"type": "text", "text": "Preview only"})
    return httpx2.Response(200, json={
        "id": INTERACTION, "status": "completed", "model": OMNI_VIDEO_MODEL,
        "steps": [{"type": "model_output", "content": [output]}],
    })


def test_omni_generation_resumes_exact_id_and_cleans_up_only_after_verified_mp4(jobs, monkeypatch):
    store, dispatched = jobs
    calls = []
    upstream_absent = False
    fail_read = True

    def handler(request):
        nonlocal upstream_absent
        assert request.headers["x-goog-api-key"] == KEY
        assert "Cookie" not in request.headers
        calls.append(request.method)
        if request.method == "POST":
            assert json.loads(request.content)["model"] == OMNI_VIDEO_MODEL
            assert json.loads(request.content)["response_format"]["duration"] == "15s"
            assert store.get(HANDLE).phase == "omni_dispatching"
            return httpx2.Response(200, json={"id": INTERACTION, "status": "in_progress"})
        assert request.url.path == f"/v1beta/interactions/{INTERACTION}"
        if request.method == "DELETE":
            assert store.get(HANDLE).state == "completed"
            metadata = store.get_media_artifacts(HANDLE)
            assert len(metadata) == 1 and Path(metadata[0]["local_path"]).read_bytes() == MP4_FIXTURE
            upstream_absent = True
            return httpx2.Response(200, json={})
        if upstream_absent:
            return httpx2.Response(404, json={"error": {"code": "not_found"}})
        return httpx2.Response(503) if fail_read else _completed()

    _mock_http(monkeypatch, handler)
    web_cleanup = AsyncMock(side_effect=AssertionError("API result used Web cleanup"))
    web_init = AsyncMock(side_effect=AssertionError("API result used Web authentication"))
    service = OperationService(OperationStore(store.path), lambda: None, web_init, web_cleanup)

    async def scenario():
        nonlocal fail_read
        start = await _submit()
        assert start.ok and start.data.operation_id == HANDLE and start.data.state == OperationState.ACCEPTED
        assert (await _submit(prompt="different prompt")).ok
        assert [job["kind"] for job in dispatched] == ["omni_video"]
        await run_worker(dispatched[0], store=store, poll_seconds=0)
        record = store.get(HANDLE)
        assert record.state == "partial" and record.upstream_operation_id == INTERACTION
        assert record.upstream_chat_id is None and record.cleanup_protected

        monkeypatch.setenv("GEMINI_API_KEY", "synthetic-other-project")
        assert (await service.get_result(HANDLE)).error.code == DomainErrorCode.CAPABILITY_UNAVAILABLE
        assert len(dispatched) == 1 and calls == ["POST", "GET"]
        monkeypatch.setenv("GEMINI_API_KEY", KEY)
        status = await service.get_status(HANDLE)
        assert status.data.upstream_operation_id == INTERACTION and not status.data.artifacts
        assert len(dispatched) == 1

        fail_read = False
        await service.get_result(HANDLE)
        assert dispatched[-1]["kind"] == "omni_video_resume" and dispatched[-1]["request"] == {}
        await run_worker(dispatched[-1], store=OperationStore(store.path), poll_seconds=0)
        result = await service.get_result(HANDLE)
        again = await service.get_result(HANDLE)
        assert result.data.state == OperationState.COMPLETED and result.data.cleanup.state == CleanupState.COMPLETED
        assert result.data.error_code is None
        assert result.data.artifacts == again.data.artifacts
        artifact = result.data.artifacts[0]
        assert artifact.kind == ArtifactKind.VIDEO and artifact.observed_backend == OMNI_VIDEO_MODEL
        assert Path(artifact.local_path).read_bytes() == MP4_FIXTURE
        assert not store.get(HANDLE).cleanup_protected
        Path(artifact.local_path).write_bytes(b"changed")
        assert (await service.get_result(HANDLE)).meta.verification_status == "local_artifact_unavailable"

    asyncio.run(scenario())
    assert calls == ["POST", "GET", "GET", "DELETE", "GET"]
    assert KEY.encode() not in store.path.read_bytes() and PROMPT.encode() not in store.path.read_bytes()
    web_cleanup.assert_not_awaited()
    web_init.assert_not_awaited()


@pytest.mark.parametrize("data, retain, expected, cleanup", [
    (None, False, "partial", "not_scheduled"),
    (b"invalid video", False, "partial", "not_scheduled"),
    (MP4_FIXTURE, True, "completed", "retained"),
])
def test_omni_keeps_retained_or_unverified_interactions(jobs, monkeypatch, data, retain, expected, cleanup):
    store, dispatched = jobs

    def handler(request):
        if request.method == "POST":
            return httpx2.Response(200, json={"id": INTERACTION})
        assert request.method == "GET"
        return _completed(data)

    _mock_http(monkeypatch, handler)
    asyncio.run(_submit(retain_chat=retain))
    asyncio.run(run_worker(dispatched[0], store=store))
    record = store.get(HANDLE)
    assert (record.state, record.cleanup_state, record.cleanup_protected) == (expected, cleanup, not retain)
    assert bool(store.get_media_artifacts(HANDLE)) is retain
    if not retain:
        assert not list(Path(record.output_dir).glob("*.mp4"))


def test_unconfirmed_omni_post_never_replays_same_handle(jobs, monkeypatch):
    store, dispatched = jobs
    calls = []

    def handler(request):
        calls.append(request.method)
        raise httpx2.ReadTimeout("upstream timed out", request=request)

    _mock_http(monkeypatch, handler)
    asyncio.run(_submit())
    asyncio.run(run_worker(dispatched[0], store=store))
    retry = asyncio.run(_submit())
    assert retry.data.state == OperationState.PARTIAL and not retry.data.continuation_possible
    assert retry.data.error_code == "TIMED_OUT" and retry.data.upstream_operation_id is None
    assert len(dispatched) == 1 and calls == ["POST"]


def test_maintenance_filters_other_owners_before_limiting_recovery(jobs, monkeypatch, tmp_path):
    store, _dispatched = jobs
    now = [100.0]
    ledger = OperationStore(store.path, clock=lambda: now[0])
    monkeypatch.setenv("GEMINI_PSID", "different-web-account")
    ledger.create_or_get("older-web-job", "media", media_type="video", effective_backend="Gemini Web video mode")
    assert ledger.claim_worker("older-web-job", "web-worker")
    assert ledger.checkpoint_media("older-web-job", worker_token="web-worker", chat_id="c_fixture",
                                   response_id="r_fixture", candidate_id="rc_fixture")
    ledger.retire_worker("older-web-job", "web-worker")
    monkeypatch.delenv("GEMINI_PSID")
    now[0] += 1
    ledger.create_or_get(HANDLE, "gemini_generate_video", media_type="video", output_dir=str(tmp_path),
                         effective_backend=resolve_media_request(OMNI_VIDEO_MODEL, "video")["backend_label"])
    assert ledger.claim_worker(HANDLE, "api-worker")
    assert ledger.begin_omni_video(HANDLE, worker_token="api-worker")
    assert ledger.checkpoint_omni_interaction(HANDLE, INTERACTION, worker_token="api-worker")
    ledger.retire_worker(HANDLE, "api-worker")
    now[0] += 61
    assert [record.operation_id for record in ledger.recoverable_media(limit=1)] == [HANDLE]
