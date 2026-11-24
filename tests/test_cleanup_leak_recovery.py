"""Regression tests for cleanup-leak recovery and worker-dispatch races.

Covers:
- `CleanupStore.schedule` tightening `delete_at` without pushing it later or
  overwriting `retained`/`completed` or an active lease.
- `run_worker` releasing `cleanup_protected` and finalizing cleanup immediately
  for terminal media operations while preserving protection for retained,
  rate-limited, and fresh recoverable operations.
- `repair_operation_cleanup` healing stuck media rows while leaving fresh and
  retained operations alone.
- `_launch_detached_worker` not mutating process-global `os.environ`.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.domain import CleanupObservation, CleanupState
from src.infrastructure.cleanup_store import CleanupStore
from src.infrastructure.operation_store import OperationRecord
from src.operation_worker import run_worker
from src.services import background
from src.services import media_jobs


# ---------------------------------------------------------------------------
# CleanupStore.schedule: delete_at tightening
# ---------------------------------------------------------------------------

def _fixed_clock(monkeypatch, value=1000.0):
    monkeypatch.setattr("src.infrastructure.cleanup_store.time.time", lambda: value)


def test_schedule_tightens_delete_at_on_non_leased_pending_row(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, 1000.0)
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    # A media job first registers a far-future pending row.
    store.schedule("acc", "c_1", 2800.0, "media_job:in_progress", retain=False)
    # A later, earlier deadline must tighten it (delete_after_seconds=0).
    store.schedule("acc", "c_1", 1000.0, "media_job:delete_after_zero", retain=False)
    row = store.get("acc", "c_1")
    assert row["state"] == "pending"
    assert row["delete_at"] == 1000.0
    assert row["source"] == "media_job:delete_after_zero"


def test_schedule_never_pushes_delete_at_later(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, 1000.0)
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("acc", "c_1", 1100.0, "early", retain=False)
    store.schedule("acc", "c_1", 3000.0, "late", retain=False)
    assert store.get("acc", "c_1")["delete_at"] == 1100.0


def test_schedule_does_not_override_retained_or_completed(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, 1000.0)
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("acc", "c_retained", 1000.0, "x", retain=True)
    store.schedule("acc", "c_retained", 1000.0, "y", retain=False)
    assert store.get("acc", "c_retained")["state"] == "retained"

    store.schedule("acc", "c_completed", 1000.0, "x", retain=False)
    token = store.claim("acc", "c_completed")
    store.finish("acc", "c_completed", token, verified=True, diagnostic_id=None)
    store.schedule("acc", "c_completed", 1000.0, "y", retain=False)
    assert store.get("acc", "c_completed")["state"] == "completed"


def test_schedule_respects_active_lease(monkeypatch, tmp_path):
    _fixed_clock(monkeypatch, 1000.0)
    store = CleanupStore(tmp_path / "cleanup.sqlite3")
    store.schedule("acc", "c_1", 1000.0, "x", retain=False)
    token = store.claim("acc", "c_1", lease_seconds=600)
    store.schedule("acc", "c_1", 1000.0, "y", retain=False)
    row = store.get("acc", "c_1")
    assert row["lease_token"] == token
    assert row["lease_until"] == 1600.0


# ---------------------------------------------------------------------------
# run_worker finalization for media / media_resume
# ---------------------------------------------------------------------------

def _media_record(*, state="running", phase="generation_observed", retain_chat=False,
                  cleanup_state="not_scheduled", error_code=None,
                  upstream_chat_id="c_0123456789abcdef", upstream_response_id="r_1",
                  upstream_candidate_id="rc_1"):
    return OperationRecord(
        operation_id="op_media", operation="gemini_create_media", state=state, phase=phase,
        created_at=1.0, updated_at=1.0, media_type="video", account_key="synthetic-owner",
        upstream_chat_id=upstream_chat_id, upstream_response_id=upstream_response_id,
        upstream_candidate_id=upstream_candidate_id, retain_chat=retain_chat,
        cleanup_state=cleanup_state, error_code=error_code,
    )


class _WorkerStore:
    def __init__(self, record):
        self.record = record
        self.token = "worker"
        self.released = False
        self.updated_state = None
        self.retired = []

    def is_worker_owner(self, _operation_id, token):
        return self.token == token

    def bind_worker(self, *_args):
        return True

    def heartbeat_worker(self, *_args):
        return True

    def get(self, _operation_id):
        return self.record

    def finish_worker(self, *_args, **_kwargs):
        return True

    def retire_worker(self, operation_id, token):
        self.retired.append((operation_id, token))
        if self.token == token:
            self.token = None

    def release_cleanup(self, operation_id):
        self.released = True

    def update_cleanup(self, operation_id, state):
        self.updated_state = state


def _set_worker_env(monkeypatch, finalize):
    import src.client_wrapper as cw

    monkeypatch.setattr(cw, "init_startup_infrastructure", lambda: None)
    monkeypatch.setattr(cw, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(cw, "_client_manager", SimpleNamespace(reset_async=AsyncMock()))
    monkeypatch.setattr("src.operation_worker.current_account_key", lambda: "synthetic-owner")
    monkeypatch.setattr("src.operation_worker.operation_account_matches", lambda _record: True)
    monkeypatch.setattr("src.operation_worker.reconcile_owned_media_cleanup", lambda *_args: False)
    monkeypatch.setattr("src.operation_worker.has_verified_owned_cleanup", lambda *_args: False)
    monkeypatch.setattr("src.operation_worker._wait_for_media_cleanup", AsyncMock())
    monkeypatch.setattr(media_jobs, "execute_media_job", AsyncMock())
    monkeypatch.setattr(cw, "finalize_remote_chat_cleanup", finalize)


async def _run_media(store):
    return await run_worker(
        {"operation_id": "op_media", "worker_token": "worker", "kind": "media", "request": {}},
        store=store, observation_seconds=0, poll_seconds=30,
    )


async def _run_media_resume(store):
    return await run_worker(
        {"operation_id": "op_media", "worker_token": "worker", "kind": "media_resume", "request": {}},
        store=store, observation_seconds=0, poll_seconds=30,
    )


def test_run_worker_releases_terminal_failed_media(monkeypatch):
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="failed", error_code="GENERATION_FAILED"))
    asyncio.run(_run_media(store))
    assert store.retired == [("op_media", "worker")]
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value
    finalize.assert_awaited_once_with(
        "c_0123456789abcdef", retain_chat=False, delete_after_seconds=0,
        source="gemini_create_media:failed",
    )


def test_run_worker_releases_terminal_unavailable_media(monkeypatch):
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="unavailable", error_code="UPSTREAM_DOWN"))
    asyncio.run(_run_media(store))
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value
    finalize.assert_awaited_once_with(
        "c_0123456789abcdef", retain_chat=False, delete_after_seconds=0,
        source="gemini_create_media:unavailable",
    )


def test_run_worker_releases_cancelled_media(monkeypatch):
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="cancelled"))
    asyncio.run(_run_media(store))
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value


def test_run_worker_releases_media_identity_incomplete_partial(monkeypatch):
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="partial", phase="media_identity_incomplete"))
    asyncio.run(_run_media(store))
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value


def test_run_worker_releases_exhausted_media_resume_partial(monkeypatch):
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    recover = AsyncMock()
    monkeypatch.setattr(media_jobs, "recover_media_job", recover)
    store = _WorkerStore(_media_record(state="partial", phase="media_readback_error"))
    asyncio.run(run_worker(
        {"operation_id": "op_media", "worker_token": "worker", "kind": "media_resume", "request": {}},
        store=store, observation_seconds=14400, poll_seconds=0,
    ))
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value


def test_run_worker_preserves_retain_chat(monkeypatch):
    finalize = AsyncMock()
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="failed", retain_chat=True))
    asyncio.run(_run_media(store))
    assert store.released is False
    assert store.updated_state is None
    finalize.assert_not_awaited()


def test_run_worker_preserves_rate_limited(monkeypatch):
    finalize = AsyncMock()
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="failed", error_code="RATE_LIMITED"))
    asyncio.run(_run_media(store))
    assert store.released is False
    assert store.updated_state is None
    finalize.assert_not_awaited()


def test_run_worker_preserves_fresh_recoverable_partial(monkeypatch):
    finalize = AsyncMock()
    _set_worker_env(monkeypatch, finalize)
    store = _WorkerStore(_media_record(state="partial", phase="media_readback_unverified",
                                       upstream_response_id="r_1", upstream_candidate_id="rc_1"))
    asyncio.run(_run_media(store))
    assert store.released is False
    assert store.updated_state is None
    finalize.assert_not_awaited()


# ---------------------------------------------------------------------------
# repair_operation_cleanup
# ---------------------------------------------------------------------------

def _repair_record(*, operation_id, state, phase, updated_at, media_type="video",
                   effective_backend="Gemini Web native browser", cleanup_protected=True,
                   retain_chat=False, worker_token=None, heartbeat_at=None,
                   error_code=None, upstream_chat_id="c_0123456789abcdef",
                   upstream_response_id=None, upstream_candidate_id=None):
    return OperationRecord(
        operation_id=operation_id, operation="gemini_create_media", state=state, phase=phase,
        created_at=1.0, updated_at=updated_at, media_type=media_type, account_key="acc",
        upstream_chat_id=upstream_chat_id, upstream_response_id=upstream_response_id,
        upstream_candidate_id=upstream_candidate_id, retain_chat=retain_chat,
        cleanup_protected=cleanup_protected, effective_backend=effective_backend,
        worker_token=worker_token, heartbeat_at=heartbeat_at, error_code=error_code,
    )


class _RepairStore:
    def __init__(self, records):
        self._records = {r.operation_id: r for r in records}
        self.released = []
        self.updated = []

    def cleanup_repair_candidates(self, account):
        return tuple(self._records.values())

    def release_cleanup(self, operation_id):
        self.released.append(operation_id)
        rec = self._records[operation_id]
        self._records[operation_id] = replace(rec, cleanup_protected=False)

    def update_cleanup(self, operation_id, state):
        self.updated.append((operation_id, state))
        rec = self._records[operation_id]
        self._records[operation_id] = replace(rec, cleanup_state=state)


def _schedule_capture():
    calls = []

    def schedule(cid, *, retain_chat, delete_after_seconds, reschedule_pending, source):
        calls.append((cid, delete_after_seconds, reschedule_pending, source))
        return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid)

    return calls, schedule


def test_repair_releases_stale_partial_media(monkeypatch):
    calls, schedule = _schedule_capture()
    observations = {"c_0123456789abcdef": CleanupObservation(state=CleanupState.PENDING)}
    old = 1000.0 - background.MEDIA_STALE_CLEANUP_RELEASE_SECONDS - 1
    store = _RepairStore([
        _repair_record(operation_id="op_stale", state="partial", phase="media_readback_unverified",
                       updated_at=old),
    ])
    monkeypatch.setattr(background, "current_account_key", lambda: "acc")
    monkeypatch.setattr(background, "operation_account_matches", lambda record: True)
    monkeypatch.setattr(background, "uses_omni_video_api", lambda record: False)
    monkeypatch.setattr(background.time, "time", lambda: 1000.0)
    repaired = background.repair_operation_cleanup(store, lambda cid: observations.get(cid), schedule)
    assert repaired == 1
    assert store.released == ["op_stale"]
    assert calls == [("c_0123456789abcdef", 0, True, "gemini_create_media:repaired")]
    assert store.updated == [("op_stale", CleanupState.PENDING.value)]


def test_repair_releases_media_identity_incomplete_partial(monkeypatch):
    calls, schedule = _schedule_capture()
    store = _RepairStore([
        _repair_record(operation_id="op_identity", state="partial", phase="media_identity_incomplete",
                       updated_at=1000.0),
    ])
    monkeypatch.setattr(background, "current_account_key", lambda: "acc")
    monkeypatch.setattr(background, "operation_account_matches", lambda record: True)
    monkeypatch.setattr(background, "uses_omni_video_api", lambda record: False)
    monkeypatch.setattr(background.time, "time", lambda: 1000.0)
    repaired = background.repair_operation_cleanup(store, lambda cid: None, schedule)
    assert repaired == 1
    assert store.released == ["op_identity"]
    assert calls[0][1] == 0 and calls[0][2] is True


def test_repair_releases_dead_zombie_running_media(monkeypatch):
    calls, schedule = _schedule_capture()
    old = 1000.0 - background.MEDIA_STALE_CLEANUP_RELEASE_SECONDS - 1
    store = _RepairStore([
        _repair_record(operation_id="op_zombie", state="running", phase="generation_observed",
                       updated_at=old, heartbeat_at=old, worker_token="dead-worker"),
    ])
    monkeypatch.setattr(background, "current_account_key", lambda: "acc")
    monkeypatch.setattr(background, "operation_account_matches", lambda record: True)
    monkeypatch.setattr(background, "uses_omni_video_api", lambda record: False)
    monkeypatch.setattr(background.time, "time", lambda: 1000.0)
    repaired = background.repair_operation_cleanup(store, lambda cid: None, schedule)
    assert repaired == 1
    assert store.released == ["op_zombie"]
    assert calls[0][1] == 0 and calls[0][2] is True


def test_repair_heals_overdue_worker_start_failed_row(monkeypatch):
    """An overdue failed row whose observation already exists (previously skipped
    by `get_observation(...) is not None`) is rescheduled for immediate cleanup."""
    calls, schedule = _schedule_capture()
    observations = {"c_0123456789abcdef": CleanupObservation(state=CleanupState.PENDING)}
    store = _RepairStore([
        _repair_record(operation_id="op_failed", state="failed", phase="dispatch_failed",
                       updated_at=1000.0, error_code="WORKER_START_FAILED",
                       cleanup_protected=False),
    ])
    monkeypatch.setattr(background, "current_account_key", lambda: "acc")
    monkeypatch.setattr(background, "operation_account_matches", lambda record: True)
    monkeypatch.setattr(background, "uses_omni_video_api", lambda record: False)
    monkeypatch.setattr(background.time, "time", lambda: 1000.0)
    repaired = background.repair_operation_cleanup(store, lambda cid: observations.get(cid), schedule)
    assert repaired == 1
    assert store.released == []
    assert calls == [("c_0123456789abcdef", 0, True, "gemini_create_media:repaired")]


def test_repair_does_not_release_fresh_recoverable_partial(monkeypatch):
    calls, schedule = _schedule_capture()
    store = _RepairStore([
        _repair_record(operation_id="op_fresh", state="partial", phase="media_readback_unverified",
                       updated_at=1000.0, upstream_response_id="r_1", upstream_candidate_id="rc_1"),
    ])
    monkeypatch.setattr(background, "current_account_key", lambda: "acc")
    monkeypatch.setattr(background, "operation_account_matches", lambda record: True)
    monkeypatch.setattr(background, "uses_omni_video_api", lambda record: False)
    monkeypatch.setattr(background.time, "time", lambda: 1000.0)
    repaired = background.repair_operation_cleanup(store, lambda cid: None, schedule)
    assert repaired == 0
    assert store.released == []
    assert calls == []


def test_repair_does_not_release_retained_op(monkeypatch):
    calls, schedule = _schedule_capture()
    old = 1000.0 - background.MEDIA_STALE_CLEANUP_RELEASE_SECONDS - 1
    store = _RepairStore([
        _repair_record(operation_id="op_retained", state="partial", phase="media_readback_unverified",
                       updated_at=old, retain_chat=True),
    ])
    monkeypatch.setattr(background, "current_account_key", lambda: "acc")
    monkeypatch.setattr(background, "operation_account_matches", lambda record: True)
    monkeypatch.setattr(background, "uses_omni_video_api", lambda record: False)
    monkeypatch.setattr(background.time, "time", lambda: 1000.0)
    repaired = background.repair_operation_cleanup(store, lambda cid: None, schedule)
    assert repaired == 0
    assert store.released == []
    assert calls == []


# ---------------------------------------------------------------------------
# _launch_detached_worker does not mutate process-global os.environ
# ---------------------------------------------------------------------------

def test_launch_detached_worker_does_not_mutate_process_environ(monkeypatch, tmp_path):
    import os

    from src.services import background

    monkeypatch.setenv("GEMINI_COOKIE_PATH", "/tmp/original-cookie-store")
    captured = {}

    class FakeStdin:
        def write(self, data):
            captured["stdin"] = data

        def close(self):
            pass

    class FakeProc:
        def __init__(self, cmd, *, stdin, stdout, stderr, env, **kwargs):
            captured["env"] = dict(env)
            self.pid = 1234
            self.stdin = FakeStdin()

        def wait(self):
            pass

    monkeypatch.setattr(background.subprocess, "Popen", FakeProc)
    monkeypatch.setattr("src.client_manager.get_extra_cookies", lambda: {})
    monkeypatch.setattr("src.cookie_manager.get_cookie_manager",
                        lambda: SimpleNamespace(get_cookie=lambda: SimpleNamespace(source="browser_chrome")))

    store = SimpleNamespace(path=tmp_path / "operations.sqlite")
    payload = {"kind": "media", "operation_id": "op_1", "worker_token": "t", "request": {}}

    # No explicit snapshot: must not mutate process-global os.environ.
    pid = background._launch_detached_worker(payload, store)
    assert pid is not None
    assert os.environ["GEMINI_COOKIE_PATH"] == "/tmp/original-cookie-store"
    assert Path(captured["env"]["GEMINI_COOKIE_PATH"]).resolve() == Path("/tmp/original-cookie-store").resolve()


# ---------------------------------------------------------------------------
# Image-tool-unavailable refusal + bounded static-partial polling
# ---------------------------------------------------------------------------

def test_image_tool_unavailable_reply_matches_live_refusals_only():
    from src.constants import is_image_tool_unavailable_reply

    assert is_image_tool_unavailable_reply(
        "I don't have an image generation tool available in this session, so I can't render the sprite sheet directly."
    )
    assert is_image_tool_unavailable_reply(
        "I cannot generate images directly in this interface—no image-generation tool is connected here."
    )
    assert is_image_tool_unavailable_reply(
        "I don't have an active image generation tool available in this interface to render the PNG sprite sheet directly."
    )
    assert not is_image_tool_unavailable_reply(
        "Note: the user quoted 'I don't have an image generation tool available in this session'."
    )
    assert not is_image_tool_unavailable_reply("Here is your generated image.")


def test_run_worker_breaks_immediately_on_media_identity_incomplete(monkeypatch):
    from src import operation_worker

    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    recover = AsyncMock()
    monkeypatch.setattr(media_jobs, "recover_media_job", recover)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(operation_worker.asyncio, "sleep", fake_sleep)
    store = _WorkerStore(_media_record(state="partial", phase="media_identity_incomplete"))
    asyncio.run(run_worker(
        {"operation_id": "op_media", "worker_token": "worker", "kind": "media", "request": {}},
        store=store, observation_seconds=14400, poll_seconds=30,
    ))
    assert sleeps == []
    recover.assert_not_awaited()
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value
    finalize.assert_awaited_once()


def test_run_worker_caps_static_partial_polls_and_releases_cleanup(monkeypatch):
    from src import operation_worker

    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_1"))
    _set_worker_env(monkeypatch, finalize)
    recover = AsyncMock()
    monkeypatch.setattr(media_jobs, "recover_media_job", recover)
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(operation_worker.asyncio, "sleep", fake_sleep)
    store = _WorkerStore(_media_record(state="partial", phase="media_artifact_not_observed"))
    asyncio.run(run_worker(
        {"operation_id": "op_media", "worker_token": "worker", "kind": "media", "request": {}},
        store=store, observation_seconds=14400, poll_seconds=30,
    ))
    assert len(sleeps) == operation_worker._MAX_MEDIA_ARTIFACT_POLLS["video"]
    assert recover.await_count == operation_worker._MAX_MEDIA_ARTIFACT_POLLS["video"]
    assert store.released is True
    assert store.updated_state == CleanupState.COMPLETED.value
    finalize.assert_awaited_once()
