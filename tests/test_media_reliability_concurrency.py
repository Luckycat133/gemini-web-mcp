"""Regression tests for media reliability, concurrency, and operation store resilience.

Covers:
- per-account ``image`` generation serialization (no external flock needed),
- checkpoint identity fallback in ``_save_response``,
- bounded readback retry in ``recover_media_job``,
- WAL mode, busy timeout and stale-worker reaping in ``OperationStore``,
- SDK retry suppression when checkpointing is active in ``media_generation``.
"""

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.domain import ArtifactKind, ArtifactState, CleanupState
from src.domain.artifacts import Artifact
from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src import client_wrapper
from src.services import media_generation, media_jobs


def _make_store(tmp_path, monkeypatch, account="synthetic-media-reliability-account"):
    monkeypatch.setenv("GEMINI_PSID", account)
    return OperationStore(tmp_path / "operations.sqlite")


def _image_record(store, operation_id="op_image", worker_token="worker"):
    store.create_or_get(
        operation_id, "gemini_generate_image", media_type="image",
        output_dir=str(store.path.parent), filename=f"{operation_id}.png",
    )
    assert store.claim_worker(operation_id, worker_token)
    return store.get(operation_id)


def _local_image_artifact(tmp_path, artifact_id="art_1"):
    path = str(tmp_path / f"{artifact_id}.png")
    return Artifact(
        id=artifact_id, kind=ArtifactKind.IMAGE, state=ArtifactState.LOCAL,
        uri="https://example.test/remote.png", title=None, local_path=path,
        mime_type="image/png", size_bytes=1,
    )


async def _run_concurrent(*coros):
    return await asyncio.gather(*coros)


# ---------------------------------------------------------------------------
# Fix 1a: _media_account_lock serializes concurrent "image" generation
# ---------------------------------------------------------------------------


def test_media_account_lock_serializes_concurrent_image_calls(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    account_key = current_account_key()
    inside = 0
    max_inside = 0
    mutex = asyncio.Lock()

    async def hold_lock():
        nonlocal inside, max_inside
        async with media_jobs._media_account_lock(
            store, account_key=account_key, media_type="image",
        ):
            async with mutex:
                inside += 1
                max_inside = max(max_inside, inside)
            await asyncio.sleep(0.05)
            async with mutex:
                inside -= 1

    asyncio.run(_run_concurrent(hold_lock(), hold_lock()))
    assert max_inside == 1, "concurrent image generation must be serialized per account"


def test_media_account_lock_does_not_block_unrelated_media_types(tmp_path, monkeypatch):
    store = _make_store(tmp_path, monkeypatch)
    account_key = current_account_key()
    inside = 0
    max_inside = 0
    mutex = asyncio.Lock()

    async def hold(media_type):
        nonlocal inside, max_inside
        async with media_jobs._media_account_lock(
            store, account_key=account_key, media_type=media_type,
        ):
            async with mutex:
                inside += 1
                max_inside = max(max_inside, inside)
            await asyncio.sleep(0.05)
            async with mutex:
                inside -= 1

    asyncio.run(_run_concurrent(hold("image"), hold("video")))
    assert max_inside == 2, "different media types use independent per-account locks"


# ---------------------------------------------------------------------------
# Fix 1b: _save_response checkpoint identity fallback
# ---------------------------------------------------------------------------


def _setup_save_response(tmp_path, monkeypatch, checkpointed=True):
    store = _make_store(tmp_path, monkeypatch)
    record = _image_record(store)
    if checkpointed:
        assert store.checkpoint_media(
            "op_image", worker_token="worker", chat_id="c_checkpointed",
            response_id="r_checkpointed", candidate_id="rc_checkpointed",
        )
    phases = []

    def record_unknown(store_arg, operation_id, token_arg, *, code=None, queued=False,
                       phase="media_outcome_unverified"):
        phases.append(phase)

    monkeypatch.setattr(media_jobs, "_unknown", record_unknown)

    saved = SimpleNamespace(
        artifacts=(_local_image_artifact(tmp_path),), failures=(),
    )
    monkeypatch.setattr(media_jobs, "_save_generated_media", AsyncMock(return_value=saved))
    monkeypatch.setattr(store, "save_media_artifacts", lambda *a, **k: True)
    monkeypatch.setattr(store, "release_media_cleanup", lambda *a, **k: True)
    monkeypatch.setattr(
        client_wrapper, "finalize_remote_chat_cleanup_from_response",
        AsyncMock(return_value=SimpleNamespace(state=CleanupState.COMPLETED, upstream_chat_id="c")),
    )
    return store, record, phases


def test_save_response_falls_back_to_checkpointed_identity(tmp_path, monkeypatch):
    """Final stream response carries images; rid/rcid are restored from the checkpoint."""
    store, record, phases = _setup_save_response(tmp_path, monkeypatch)
    response = SimpleNamespace(
        metadata=["c_checkpointed"], text="", images=[object()], videos=[], media=[], candidates=[],
    )
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert "media_identity_incomplete" not in phases, phases
    assert store.get("op_image").state == "completed"


def test_save_response_text_only_missing_rcid_no_checkpoint_fallback(tmp_path, monkeypatch):
    """Text-only response with missing rcid does NOT use checkpoint fallback."""
    store, record, phases = _setup_save_response(tmp_path, monkeypatch)
    response = SimpleNamespace(
        metadata=["c_checkpointed"], text="hello", images=[], videos=[], media=[], candidates=[],
    )
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert phases == ["media_identity_incomplete"], phases


def test_save_response_fills_all_identity_from_checkpoint_when_response_cid_missing(tmp_path, monkeypatch):
    """A response cid of None is populated entirely from the durably checkpointed identity."""
    store, record, phases = _setup_save_response(tmp_path, monkeypatch)
    response = SimpleNamespace(
        metadata=[], text="", images=[SimpleNamespace(url="https://example.com/img.png")], videos=[], media=[], candidates=[],
    )
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert "media_identity_incomplete" not in phases, phases
    assert store.get("op_image").state == "completed"


def test_save_response_refuses_conflicting_chat_id(tmp_path, monkeypatch):
    """A response CID that differs from the checkpointed chat is never mixed in."""
    store, record, phases = _setup_save_response(tmp_path, monkeypatch)
    response = SimpleNamespace(
        metadata=["c_foreign"], text="", images=[], videos=[], media=[], candidates=[],
    )
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert phases == ["media_identity_incomplete"], phases


def test_save_response_fails_when_checkpoint_also_missing(tmp_path, monkeypatch):
    """No response identity and no checkpointed identity means media_identity_incomplete."""
    store, record, phases = _setup_save_response(tmp_path, monkeypatch, checkpointed=False)
    response = SimpleNamespace(
        metadata=["c_only"], text="", images=[], videos=[], media=[], candidates=[],
    )
    asyncio.run(media_jobs._save_response(object(), response, record, store=store, token="worker"))
    assert phases == ["media_identity_incomplete"], phases


# ---------------------------------------------------------------------------
# Fix 1c: recover_media_job bounded readback retry
# ---------------------------------------------------------------------------


class _ReadbackClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def read_chat(self, cid, limit=10):
        self.calls += 1
        if self.outcomes:
            return self.outcomes.pop(0)
        return None


def _matching_history(cid="c_recover", rid="r_recover", rcid="rc_recover"):
    candidate = SimpleNamespace(rcid=rcid)
    response = SimpleNamespace(metadata=[cid, rid], candidates=[candidate])
    turn = SimpleNamespace(role="model", model_output=response)
    return SimpleNamespace(turns=[turn])


def _setup_recover(tmp_path, monkeypatch, client):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-recover-account")
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create_or_get(
        "op_recover", "gemini_generate_image", media_type="image",
        output_dir=str(store.path.parent), filename="op_recover.png",
    )
    assert store.claim_worker("op_recover", "worker")
    assert store.checkpoint_media(
        "op_recover", worker_token="worker", chat_id="c_recover",
        response_id="r_recover", candidate_id="rc_recover",
    )
    record = store.get("op_recover")
    phases = []
    monkeypatch.setattr(media_jobs, "_unknown", lambda s, op, t, **k: phases.append(k.get("phase")))
    monkeypatch.setattr(media_jobs, "reconcile_owned_media_cleanup", lambda s, r: False)
    monkeypatch.setattr(media_jobs, "_save_response", AsyncMock())
    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", AsyncMock())
    # Instant backoff so the retry loop is fast in tests.
    monkeypatch.setattr(media_jobs, "MEDIA_READBACK_RETRY_DELAYS", (0, 0))
    return store, record, phases


def test_recover_media_job_retries_transient_connection_error(tmp_path, monkeypatch):
    """Transient OSError on first read is retried; matching turn on attempt 2 succeeds."""
    class _ErrorClient:
        def __init__(self, outcomes):
            self.outcomes = list(outcomes)
            self.calls = 0

        async def read_chat(self, cid, limit=10):
            self.calls += 1
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    client = _ErrorClient([OSError("transient network error"), _matching_history()])
    store, record, phases = _setup_recover(tmp_path, monkeypatch, client)
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="worker"))
    assert client.calls == 2, client.calls
    assert "media_readback_unverified" not in phases, phases
    media_jobs._save_response.assert_awaited_once()


def test_recover_media_job_none_history_immediate_readback_unverified(tmp_path, monkeypatch):
    """None history reports media_readback_unverified immediately, no retry, no fabrication."""
    client = _ReadbackClient([None])
    store, record, phases = _setup_recover(tmp_path, monkeypatch, client)
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="worker"))
    assert client.calls == 1, client.calls
    assert phases == ["media_readback_unverified"], phases
    media_jobs._save_response.assert_not_awaited()


def test_recover_media_job_never_fabricates_success_when_all_reads_fail(tmp_path, monkeypatch):
    """All attempts return None: reports media_readback_unverified on first attempt, never saves."""
    client = _ReadbackClient([None, None, None])
    store, record, phases = _setup_recover(tmp_path, monkeypatch, client)
    asyncio.run(media_jobs.recover_media_job(record, store=store, worker_token="worker"))
    assert client.calls == 1, client.calls
    assert phases == ["media_readback_unverified"], phases
    media_jobs._save_response.assert_not_awaited()


# ---------------------------------------------------------------------------
# Fix 3a/3b: OperationStore WAL, busy timeout, reap_stale_workers
# ---------------------------------------------------------------------------


def test_operation_store_uses_wal_mode_on_disk_db(tmp_path, monkeypatch):
    _make_store(tmp_path, monkeypatch)
    timeouts = []
    real_connect = sqlite3.connect

    def capturing_connect(path, *args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr("src.infrastructure.operation_store.sqlite3.connect", capturing_connect)
    store = OperationStore(tmp_path / "operations.sqlite")
    store.create("op_wal", "gemini_research")
    assert 30.0 in timeouts, timeouts
    with sqlite3.connect(store.path) as db:
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_reap_stale_workers_clears_expired_leases_keeps_active(tmp_path, monkeypatch):
    now = [1000.0]
    store = OperationStore(tmp_path / "operations.sqlite", clock=lambda: now[0])
    store.create_or_get("op_stale", "gemini_generate_image", media_type="image")
    store.create_or_get("op_active", "gemini_generate_image", media_type="image")
    assert store.claim_worker("op_stale", "token-stale")
    assert store.claim_worker("op_active", "token-active")
    store.update("op_stale", state="running", phase="running")
    store.update("op_active", state="running", phase="running")
    # Stale: heartbeat well older than the 60s default.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET heartbeat_at=? WHERE operation_id='op_stale'", (now[0] - 120,))
        db.execute("UPDATE operations SET heartbeat_at=? WHERE operation_id='op_active'", (now[0],))
        db.commit()
    reaped = store.reap_stale_workers()
    assert reaped == 1
    assert store.get("op_stale").worker_token is None
    assert store.get("op_stale").worker_pid is None
    assert store.get("op_stale").heartbeat_at is None
    # Active heartbeat untouched.
    assert store.get("op_active").worker_token == "token-active"
    assert store.get("op_active").heartbeat_at is not None


def test_reap_stale_workers_leaves_terminal_states_alone(tmp_path, monkeypatch):
    now = [1000.0]
    store = OperationStore(tmp_path / "operations.sqlite", clock=lambda: now[0])
    store.create_or_get("op_done", "gemini_generate_image", media_type="image")
    # Manually place an old lease on a terminal (completed) row.
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE operations SET state='completed', worker_token='token-done', "
            "heartbeat_at=? WHERE operation_id='op_done'", (now[0] - 120,),
        )
        db.commit()
    assert store.reap_stale_workers() == 0
    assert store.get("op_done").worker_token == "token-done"


# ---------------------------------------------------------------------------
# Fix 2: media_generation suppresses SDK retries when checkpointing is active
# ---------------------------------------------------------------------------


def _run_generate(tmp_path, *, checkpoint_active, has_factory, monkeypatch):
    _make_store(tmp_path, monkeypatch)
    seen = {}

    class Client:
        async def generate_content_stream(self, **kwargs):
            seen.update(kwargs)
            yield SimpleNamespace(metadata=[], text="done", images=[], videos=[], media=[])

    if has_factory:
        Client.create_cleanup_chat = staticmethod(lambda client: SimpleNamespace())

    asyncio.run(media_generation.generate_media_response(
        Client(), arguments={"prompt": "x", "model": "flash", "timeout": 30},
        schedule_cleanup=lambda *a, **k: None, retain_chat=False,
        delete_after_seconds=None, source="test",
        checkpoint=(lambda r: None) if checkpoint_active else None,
    ))
    return seen


def test_media_generation_sets_current_retry_zero_when_checkpoint_active(tmp_path, monkeypatch):
    seen = _run_generate(tmp_path, checkpoint_active=True, has_factory=False, monkeypatch=monkeypatch)
    assert seen.get("current_retry") == 0, seen


def test_media_generation_sets_current_retry_zero_when_factory_present(tmp_path, monkeypatch):
    seen = _run_generate(tmp_path, checkpoint_active=False, has_factory=True, monkeypatch=monkeypatch)
    assert seen.get("current_retry") == 0, seen


def test_media_generation_does_not_set_current_retry_without_checkpoint_or_factory(tmp_path, monkeypatch):
    seen = _run_generate(tmp_path, checkpoint_active=False, has_factory=False, monkeypatch=monkeypatch)
    assert "current_retry" not in seen, seen
