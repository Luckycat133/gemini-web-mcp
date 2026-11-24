"""Media recovery stops only at exact, independently verified cleanup evidence."""

import asyncio
import sqlite3
from unittest.mock import AsyncMock

import pytest

from src.constants import NATIVE_MEDIA_BROWSER_BACKEND, OMNI_VIDEO_API_BACKEND
from src.domain.artifacts import Artifact, ArtifactKind, ArtifactState
from src.infrastructure.account_identity import current_account_keys
from src.infrastructure.cleanup_store import CleanupStore
from src.infrastructure.operation_store import OperationStore
from src.services import background
from src.services.media_cleanup import has_verified_owned_cleanup, reconcile_owned_media_cleanup
from src.services.operations import OperationService


_OPERATION = "op_" + "a" * 32
_CHAT = "c_0123456789abcdef"
_IDENTITY = (_CHAT, "r_synthetic_original", "rc_synthetic_original")


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-owned-cleanup-account")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-omni-key")
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite"))
    now = [1000.0]
    return OperationStore(tmp_path / "operations.sqlite", clock=lambda: now[0]), CleanupStore(), now


def _seed(store, *, protected=False, retained=False, backend=NATIVE_MEDIA_BROWSER_BACKEND):
    store.create_or_get(_OPERATION, "gemini_create_media", media_type="video", retain_chat=retained,
                        effective_backend=backend, output_dir=str(store.path.parent), filename="synthetic")
    assert store.claim_worker(_OPERATION, "original")
    assert store.bind_worker(_OPERATION, "original", 1234)
    assert store.checkpoint_media(_OPERATION, worker_token="original", chat_id=_CHAT,
                                  response_id=_IDENTITY[1], candidate_id=_IDENTITY[2])
    assert store.finish_worker(_OPERATION, worker_token="original", state="partial", phase="media_readback_unverified",
                               verification_status="not_observed", error_code="READBACK_UNVERIFIED")
    if not protected:
        store.release_cleanup(_OPERATION)
    store.update_cleanup(_OPERATION, "pending")
    return store.get(_OPERATION)


def _proof(cleanup, account, cid=_CHAT):
    cleanup.schedule(account, cid, 0, "synthetic", retain=False)
    assert cleanup.record_verified_absence(account, cid)


def test_resume_stops_original_observer_and_persists_confirmed_absence(stores, monkeypatch):
    store, cleanup, now = stores
    record = _seed(store)
    _proof(cleanup, record.account_key)
    before_proof = cleanup.get(record.account_key, _CHAT)
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)

    result = asyncio.run(background.resume_media_worker(store, record))

    assert not result.ok and result.error_code == "CAPABILITY_UNAVAILABLE"
    assert result.data.operation_id == _OPERATION and not result.data.continuation_possible
    saved = store.get(_OPERATION)
    assert (saved.upstream_chat_id, saved.upstream_response_id, saved.upstream_candidate_id) == _IDENTITY
    assert saved.cleanup_state == "completed" and not saved.cleanup_protected
    assert saved.state == "unavailable" and saved.verification_status == "remote_media_already_cleaned"
    assert saved.worker_token is saved.worker_pid is saved.heartbeat_at is None
    now[0] += 90
    assert not store.recoverable_media()
    assert cleanup.get(record.account_key, _CHAT) == before_proof
    assert reconcile_owned_media_cleanup(store, saved)
    assert store.get(_OPERATION) == saved  # Repeated reads leave the reconciled row untouched.
    dispatch.assert_not_awaited()


def test_result_reloads_persisted_cleanup_while_status_only_reads_evidence(stores, monkeypatch):
    store, cleanup, _ = stores
    record = _seed(store)
    _proof(cleanup, record.account_key)
    store.retire_worker(_OPERATION, "original")
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    initializer, finalize = AsyncMock(), AsyncMock()
    service = OperationService(store, lambda: None, initializer, finalize)

    status = asyncio.run(service.get_status(_OPERATION))
    assert status.data.cleanup.state.value == "completed" and not status.data.continuation_possible
    assert store.get(_OPERATION).cleanup_state == "pending"
    result = asyncio.run(service.get_result(_OPERATION))
    assert result.data.cleanup.state.value == "completed" and not result.data.continuation_possible
    assert result.error_code == "CAPABILITY_UNAVAILABLE" and store.get(_OPERATION).state == "unavailable"
    dispatch.assert_not_awaited()
    initializer.assert_not_awaited()
    finalize.assert_not_awaited()


@pytest.mark.parametrize("state", ["failed", "partial"])
def test_cleanup_preserves_confirmed_rejection_and_its_verification(stores, state):
    store, cleanup, _ = stores
    record = _seed(store)
    assert store.finish_worker(_OPERATION, worker_token="original", state=state, phase="media_rejected",
                               verification_status="completed_music_notation_without_artifact_observed",
                               error_code="ARTIFACT_NOT_RETURNED")
    _proof(cleanup, record.account_key)

    assert reconcile_owned_media_cleanup(store, store.get(_OPERATION))

    saved = store.get(_OPERATION)
    assert saved.state == "failed" and saved.phase == "media_rejected"
    assert saved.verification_status == "completed_music_notation_without_artifact_observed"
    assert saved.error_code == "ARTIFACT_NOT_RETURNED" and saved.cleanup_state == "completed"


@pytest.mark.parametrize("saved_state", ["completed", "partial"])
def test_cleanup_preserves_existing_artifact_metadata_without_changing_result_state(stores, tmp_path, saved_state):
    store, cleanup, _ = stores
    record = _seed(store)
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"offline artifact fixture")
    artifact = Artifact(id="artifact", kind=ArtifactKind.VIDEO, state=ArtifactState.LOCAL,
                        local_path=str(path), mime_type="video/mp4")
    assert store.save_media_artifacts(_OPERATION, (artifact,), worker_token="original", artifact_state="local",
                                      verification_status="local_artifact_verified")
    if saved_state == "partial":
        # Compat fixture for old ledgers with artifact metadata and a partial operation.
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE operations SET state='partial' WHERE operation_id=?", (_OPERATION,))
    artifacts = store.get_media_artifacts(_OPERATION)
    _proof(cleanup, record.account_key)

    assert reconcile_owned_media_cleanup(store, store.get(_OPERATION))

    saved = store.get(_OPERATION)
    assert saved.cleanup_state == "completed" and saved.state == saved_state
    assert saved.artifact_state == "local" and saved.verification_status == "local_artifact_verified"
    assert store.get_media_artifacts(_OPERATION) == artifacts and path.read_bytes() == b"offline artifact fixture"


@pytest.mark.parametrize("condition", [
    "missing", "pending", "protected", "retained", "foreign", "other_chat", "omni", "read_error",
])
def test_ineligible_or_unverified_cleanup_never_changes_operation(stores, monkeypatch, condition):
    store, cleanup, _ = stores
    record = _seed(store, protected=condition == "protected", retained=condition == "retained",
                   backend=OMNI_VIDEO_API_BACKEND if condition == "omni" else NATIVE_MEDIA_BROWSER_BACKEND)
    if condition == "pending":
        cleanup.schedule(record.account_key, _CHAT, 0, "synthetic", retain=False)
    elif condition != "missing":
        _proof(cleanup, record.account_key, "c_1111111111111111" if condition == "other_chat" else _CHAT)
    if condition == "foreign":
        monkeypatch.setenv("GEMINI_PSID", "other-synthetic-account")

    def sqlite_failure(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic database lock")

    if condition == "read_error":
        monkeypatch.setattr(CleanupStore, "get", sqlite_failure)
    assert not reconcile_owned_media_cleanup(store, record)
    assert store.get(_OPERATION) == record


@pytest.mark.parametrize("changed", ["token", "timestamp"])
def test_verified_cleanup_does_not_overwrite_a_changed_operation_snapshot(stores, changed):
    store, cleanup, now = stores
    old = _seed(store)
    _proof(cleanup, old.account_key)
    if changed == "token":
        store.retire_worker(_OPERATION, "original")
        assert store.claim_worker(_OPERATION, "new-lease")
    else:
        now[0] += 1
        assert store.finish_worker(_OPERATION, worker_token="original", state="queued", phase="recent_checkpoint",
                                   verification_status="not_observed")
    current = store.get(_OPERATION)

    assert not store.reconcile_media_cleanup(old)
    assert reconcile_owned_media_cleanup(store, old)  # Skip the stale attempt without changing the newer row.
    assert store.get(_OPERATION) == current
    assert reconcile_owned_media_cleanup(store, current)
    assert store.get(_OPERATION).cleanup_state == "completed"


def test_cleanup_uses_only_the_verified_same_psid_storage_alias(stores):
    store, cleanup, _ = stores
    canonical, legacy = current_account_keys()
    _seed(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET account_key=? WHERE operation_id=?", (legacy, _OPERATION))
    record = store.get(_OPERATION)
    _proof(cleanup, canonical)
    assert has_verified_owned_cleanup(record)
    assert reconcile_owned_media_cleanup(store, record)
    assert store.get(_OPERATION).account_key == legacy


@pytest.mark.parametrize("failure", [sqlite3.OperationalError("synthetic metadata lock"), OSError("synthetic local I/O")])
def test_confirmed_cleanup_still_stops_read_if_local_reconciliation_cannot_be_saved(stores, monkeypatch, failure):
    store, cleanup, _ = stores
    record = _seed(store)
    _proof(cleanup, record.account_key)
    before = store.get(_OPERATION)

    def cannot_save(_record):
        raise failure

    monkeypatch.setattr(store, "reconcile_media_cleanup", cannot_save)
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    assert reconcile_owned_media_cleanup(store, record)
    asyncio.run(background.resume_media_worker(store, record))
    assert store.get(_OPERATION) == before
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("policy", ["protected", "retained"])
def test_stale_operation_cleanup_field_does_not_block_protected_or_retained_web_recovery(stores, monkeypatch, policy):
    store, cleanup, now = stores
    _seed(store, protected=policy == "protected", retained=policy == "retained")
    store.update_cleanup(_OPERATION, "completed")
    store.retire_worker(_OPERATION, "original")
    now[0] += 90
    current = store.get(_OPERATION)
    assert cleanup.get(current.account_key, _CHAT) is None
    assert [item.operation_id for item in store.recoverable_media()] == [_OPERATION]
    dispatch = AsyncMock(return_value="recovery-dispatched")
    monkeypatch.setattr(background, "_dispatch", dispatch)
    result = asyncio.run(background.resume_media_worker(store, current))
    assert result == "recovery-dispatched"
    dispatch.assert_awaited_once_with(store, current, "media_resume")
    assert store.get(_OPERATION) == current
