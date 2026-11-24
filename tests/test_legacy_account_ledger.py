"""The same credential can resume v6 rows written with the earlier account digest."""

import asyncio
from contextlib import contextmanager
import hashlib
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.constants import OMNI_VIDEO_API_BACKEND
from src.infrastructure.account_identity import current_account_key
from src.infrastructure.operation_store import OperationStore
from src.services.research_reports import resolve_research_report
from src.services.research_result_storage import save_research_result
from src.services.task_result_storage import save_task_result


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    psid = "synthetic-v6-shared-runtime-account"
    monkeypatch.setenv("GEMINI_PSID", psid)
    legacy = hashlib.pbkdf2_hmac("sha256", psid.encode(), b"gemini-psid", 10_000).hex()
    now = [1000.0]
    return OperationStore(tmp_path / "operations.sqlite", clock=lambda: now[0]), legacy, now


def _bind_legacy(store, operation_id, legacy):
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET account_key=? WHERE operation_id=?", (legacy, operation_id))


def _task(store, operation_id, chat_id="c_fixture"):
    store.create(operation_id, "gemini_run_task")
    assert store.claim_worker(operation_id, "owner")
    assert store.checkpoint_media(
        operation_id, worker_token="owner", chat_id=chat_id, response_id="r_fixture", candidate_id="rc_fixture",
    )


def test_web_recovery_and_repair_use_both_keys_and_preserve_legacy_guard(ledger, monkeypatch):
    store, legacy, now = ledger
    _task(store, "op_legacy", chat_id="c_legacy")
    _task(store, "op_guarded", chat_id="c_guarded")
    _bind_legacy(store, "op_legacy", legacy)
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO task_schedule_guards VALUES (?, 'schedule_old', 'c_guarded', ?)", (legacy, now[0]))
    now[0] += 61

    assert [record.operation_id for record in store.recoverable_web_workflows()] == ["op_legacy"]
    assert {record.operation_id for record in store.cleanup_repair_candidates(current_account_key())} == {
        "op_legacy", "op_guarded",
    }
    assert store.has_owned_task_chat(current_account_key(), "c_legacy")
    assert store.get("op_guarded").cleanup_protected and store.get("op_legacy").account_key == legacy

    monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    assert store.recoverable_web_workflows() == ()
    assert store.cleanup_repair_candidates(current_account_key()) == ()
    assert not store.has_owned_task_chat(current_account_key(), "c_legacy")
    assert store.observe_task_deletion("c_legacy") == 0
    monkeypatch.delenv("GEMINI_PSID")
    assert store.recoverable_web_workflows() == ()
    assert store.is_cleanup_protected("c_guarded")


def test_legacy_task_input_requires_current_credential_and_exact_worker(ledger, monkeypatch):
    store, legacy, _now = ledger
    _task(store, "op_legacy")
    _bind_legacy(store, "op_legacy", legacy)
    bound = {"response_id": "r_fixture", "candidate_id": "rc_fixture", "worker_token": "next"}
    assert not store.begin_task_input("op_legacy", "input_fixture", expected_worker_token="wrong", **bound)
    with monkeypatch.context() as different:
        different.setenv("GEMINI_PSID", "synthetic-other-account")
        assert not store.begin_task_input("op_legacy", "input_fixture", expected_worker_token="owner", **bound)
    assert store.begin_task_input("op_legacy", "input_fixture", expected_worker_token="owner", **bound)
    assert store.task_input_dispatched("op_legacy", "input_fixture") and store.has_task_input("op_legacy")
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT account_key FROM task_inputs WHERE operation_id='op_legacy'").fetchone() == (legacy,)
    assert store.get("op_legacy").account_key == legacy
    monkeypatch.delenv("GEMINI_PSID")
    assert not store.task_input_dispatched("op_legacy", "input_fixture") and not store.has_task_input("op_legacy")


def test_web_approval_claims_are_deduplicated_across_owner_formats(ledger):
    store, legacy, _now = ledger
    owner = current_account_key()
    assert store.claim_web_task_approval(legacy, "c_web", "r_one", "rc_one", title_digest="same-title")
    assert store.has_web_task_delete_claim(owner, "c_web", "same-title")
    assert not store.claim_web_task_approval(owner, "c_web", "r_one", "rc_one")
    assert not store.claim_web_task_approval(owner, "c_web", "r_two", "rc_two", title_digest="same-title")
    store.finish_web_task_approval(legacy, "c_web", "r_one", "rc_one", continuation_verified=True)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT account_key,state FROM web_task_approval_claims").fetchall() == [
            (owner, "continuation_observed"),
        ]


def _seed_legacy_approval(store, legacy, *, title="same-title"):
    with store._connect() as db:
        db.execute("INSERT INTO web_task_approval_claims VALUES (?,'c_web','r_one','rc_one','claimed')", (legacy,))
        db.execute("INSERT INTO web_task_delete_claims VALUES (?,'c_web',?)", (legacy, title))


def _approval_rows(store):
    with sqlite3.connect(store.path) as db:
        return (db.execute("SELECT * FROM web_task_approval_claims ORDER BY account_key").fetchall(),
                db.execute("SELECT * FROM web_task_delete_claims ORDER BY account_key").fetchall())


def test_canonical_finish_preserves_legacy_approval_binding_and_monotonic_state(ledger):
    store, legacy, _now = ledger
    _seed_legacy_approval(store, legacy)
    store.finish_web_task_approval(current_account_key(), "c_web", "r_other", "rc_one", continuation_verified=True)
    assert _approval_rows(store)[0] == [(legacy, "c_web", "r_one", "rc_one", "claimed")]
    store.finish_web_task_approval(current_account_key(), "c_web", "r_one", "rc_one", continuation_verified=True)
    assert _approval_rows(store)[0] == [(legacy, "c_web", "r_one", "rc_one", "continuation_observed")]
    store.finish_web_task_approval(legacy, "c_web", "r_one", "rc_one", continuation_verified=False)
    assert _approval_rows(store)[0][0][-1] == "continuation_observed"


def test_canonical_positive_no_click_release_removes_only_exact_legacy_claim_and_rule(ledger):
    store, legacy, _now = ledger
    _seed_legacy_approval(store, legacy)
    with store._connect() as db:
        db.execute("INSERT INTO web_task_delete_claims VALUES (?,'c_web','unrelated-title')", (legacy,))
        db.execute("INSERT INTO web_task_delete_claims VALUES ('foreign','c_web','same-title')")
    before = _approval_rows(store)
    store.release_unmatched_web_task_delete(current_account_key(), "c_web", "r_one", "rc_other", title_digest="same-title")
    assert _approval_rows(store) == before
    store.release_unmatched_web_task_delete(current_account_key(), "c_web", "r_one", "rc_one", title_digest="same-title")
    assert _approval_rows(store) == ([], sorted([
        (legacy, "c_web", "unrelated-title"), ("foreign", "c_web", "same-title"),
    ]))
    assert store.claim_web_task_approval(legacy, "c_web", "r_one", "rc_one", title_digest="same-title")
    assert _approval_rows(store)[0][0][0] == current_account_key()


@pytest.mark.parametrize("conflict", ["approval", "delete_rule"])
def test_alias_collision_blocks_approval_finish_and_release_without_overwrite(ledger, conflict):
    store, legacy, _now = ledger
    _seed_legacy_approval(store, legacy)
    canonical = current_account_key()
    with store._connect() as db:
        if conflict == "approval":
            db.execute("INSERT INTO web_task_approval_claims VALUES (?,'c_web','r_one','rc_one','claimed')", (canonical,))
        else:
            db.execute("INSERT INTO web_task_delete_claims VALUES (?,'c_web','same-title')", (canonical,))
    before = _approval_rows(store)
    store.release_unmatched_web_task_delete(canonical, "c_web", "r_one", "rc_one", title_digest="same-title")
    assert _approval_rows(store) == before
    if conflict == "approval":
        store.finish_web_task_approval(canonical, "c_web", "r_one", "rc_one", continuation_verified=True)
        assert _approval_rows(store) == before
    assert not store.claim_web_task_approval(canonical, "c_web", "r_one", "rc_one", title_digest="same-title")


@pytest.mark.parametrize("supplied", [None, "", "unknown", "legacy"])
def test_foreign_or_empty_owner_cannot_finish_or_release_legacy_claim(ledger, monkeypatch, supplied):
    store, legacy, _now = ledger
    _seed_legacy_approval(store, legacy)
    before = _approval_rows(store)
    if supplied == "legacy":
        supplied = legacy
        monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    store.finish_web_task_approval(supplied, "c_web", "r_one", "rc_one", continuation_verified=True)
    store.release_unmatched_web_task_delete(supplied, "c_web", "r_one", "rc_one", title_digest="same-title")
    assert not store.claim_web_task_approval(supplied, "c_new", "r_new", "rc_new")
    assert _approval_rows(store) == before


@pytest.mark.parametrize("method", ["claim", "finish", "release"])
def test_account_change_during_approval_write_rolls_back_exact_legacy_scope(ledger, monkeypatch, method):
    store, legacy, _now = ledger
    _seed_legacy_approval(store, legacy)
    before = _approval_rows(store)
    canonical = current_account_key()
    original = store._connect
    trigger = {"claim": "INSERT OR IGNORE INTO web_task_approval_claims",
               "finish": "UPDATE web_task_approval_claims", "release": "DELETE FROM web_task_approval_claims"}[method]

    @contextmanager
    def switch_during_write(**options):
        with original(**options) as db:
            def trace(statement):
                if statement.lstrip().startswith(trigger):
                    monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")

            db.set_trace_callback(trace)
            yield db

    monkeypatch.setattr(store, "_connect", switch_during_write)
    with pytest.raises(PermissionError, match="account changed"):
        if method == "claim":
            store.claim_web_task_approval(canonical, "c_new", "r_new", "rc_new", title_digest="new-title")
        elif method == "finish":
            store.finish_web_task_approval(canonical, "c_web", "r_one", "rc_one", continuation_verified=True)
        else:
            store.release_unmatched_web_task_delete(canonical, "c_web", "r_one", "rc_one", title_digest="same-title")
    assert _approval_rows(store) == before


def test_native_cid_only_checkpoint_preserves_submission_provenance_until_exact_reply(ledger):
    store, _legacy, _now = ledger
    store.create_or_get("op_native", "gemini_generate_music", media_type="music")
    assert store.claim_worker("op_native", "worker")
    assert not store.checkpoint_media("op_native", worker_token="wrong", chat_id="c_native", native_submission=True)
    assert store.checkpoint_media("op_native", worker_token="worker", chat_id="c_native", native_submission=True)
    assert store.get("op_native").phase == "native_media_submitted"
    assert store.get("op_native").upstream_response_id is None
    assert store.checkpoint_media("op_native", worker_token="worker", chat_id="c_native")
    assert store.get("op_native").phase == "native_media_submitted"
    assert store.checkpoint_media("op_native", worker_token="worker", chat_id="c_native", response_id="r_native", candidate_id="rc_native")
    assert store.get("op_native").phase == "generation_observed"
    with pytest.raises(ValueError, match="identity conflicts"):
        store.checkpoint_media("op_native", worker_token="worker", chat_id="c_other", native_submission=True)


def test_default_cid_only_checkpoint_retains_existing_generation_phase(ledger):
    store, _legacy, _now = ledger
    store.create_or_get("op_legacy_transport", "gemini_generate_image", media_type="image")
    assert store.claim_worker("op_legacy_transport", "worker")
    assert store.checkpoint_media("op_legacy_transport", worker_token="worker", chat_id="c_default")
    assert store.get("op_legacy_transport").phase == "generation_observed"


def test_legacy_task_and_manifest_can_save_and_reopen_under_same_account(ledger, monkeypatch):
    store, legacy, _now = ledger
    _task(store, "op_legacy")
    _bind_legacy(store, "op_legacy", legacy)
    save_task_result(store, store.get("op_legacy"), "Synthetic Task report on an existing operation.")
    store.commit_task_files("op_legacy", response_id="r_fixture", candidate_id="rc_fixture", artifacts=())
    assert store.task_files_committed("op_legacy")
    assert store.checkpoint_task_result_state("op_legacy", terminal_state="completed")
    with monkeypatch.context() as different:
        different.setenv("GEMINI_PSID", "synthetic-other-account")
        assert not store.task_files_committed("op_legacy")
        assert not store.release_task_result_cleanup("op_legacy")
    assert store.get("op_legacy").cleanup_protected
    assert store.release_task_result_cleanup("op_legacy")
    assert [row.account_key for row in store.disposable_task_records("c_fixture")] == [legacy]


def test_legacy_saved_research_stays_local_and_rejects_other_accounts(ledger, monkeypatch):
    store, legacy, _now = ledger
    store.create("op_research", "gemini_research")
    _bind_legacy(store, "op_research", legacy)
    assert store.attach_research_observation("op_research", "c_stored", account_key=current_account_key(),
                                            response_id="r_stored", candidate_id="rc_stored")
    text = "# Saved Research\n\nThis is a report from the older shared runtime."
    report = save_research_result(store, store.get("op_research"), SimpleNamespace(ready=True, content=text))
    assert report and store.get("op_research").account_key == legacy
    initialize, fetch, provider = AsyncMock(), AsyncMock(), Mock()
    source, failure = asyncio.run(resolve_research_report(
        chat_id=None, operation_id="op_research", store=store, client_initializer=initialize,
        client_provider=provider, fetch_report=fetch,
    ))
    assert failure is None and source and source.account_matches() and source.output.text == text
    initialize.assert_not_called()
    fetch.assert_not_called()
    provider.assert_not_called()
    monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    assert not source.account_matches()


def test_legacy_web_media_recovery_does_not_alias_paid_api_owner(ledger, monkeypatch):
    store, legacy, now = ledger
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-api-key")
    store.create_or_get("op_web", "gemini_generate_video", media_type="video")
    store.create_or_get("op_api", "gemini_generate_video", media_type="video", effective_backend=OMNI_VIDEO_API_BACKEND)
    store.create_or_get("op_wrong_owner", "gemini_generate_video", media_type="video", effective_backend=OMNI_VIDEO_API_BACKEND)
    _bind_legacy(store, "op_web", legacy)
    _bind_legacy(store, "op_wrong_owner", legacy)
    assert store.claim_worker("op_web", "owner")
    assert store.checkpoint_media("op_web", worker_token="owner", chat_id="c_media", response_id="r_media", candidate_id="rc_media")
    for operation_id in ("op_api", "op_wrong_owner"):
        store.update(operation_id, state="running", phase="generation_observed", upstream_operation_id="interaction_fixture")
    now[0] += 61
    assert {record.operation_id for record in store.recoverable_media()} == {"op_web", "op_api"}
    monkeypatch.delenv("GEMINI_PSID")
    assert [record.operation_id for record in store.recoverable_media()] == ["op_api"]
    monkeypatch.delenv("GEMINI_API_KEY")
    assert store.recoverable_media() == ()


def test_legacy_image_is_a_source_only_for_its_own_account(ledger, monkeypatch):
    store, legacy, _now = ledger
    store.create_or_get("op_parent", "gemini_generate_image", media_type="image")
    _bind_legacy(store, "op_parent", legacy)
    store.update("op_parent", state="completed", phase="artifacts_saved")
    with sqlite3.connect(store.path) as db:
        db.execute("INSERT INTO operation_artifacts VALUES ('op_parent', 'image_fixture', 'image', 'image/png', ?, 5, ?)",
                   (str(store.path.parent / "fixture.png"), "a" * 64))

    source = {"source_operation_id": "op_parent", "source_artifact_id": "image_fixture"}
    child, created = store.create_or_get("op_edit", "gemini_edit_image", media_type="image", **source)
    assert created and child.source_operation_id == "op_parent" and child.source_artifact_id == "image_fixture"
    assert store.get("op_parent").account_key == legacy
    monkeypatch.setenv("GEMINI_PSID", "synthetic-other-account")
    with pytest.raises(PermissionError):
        store.create_or_get("op_other", "gemini_edit_image", media_type="image", **source)
    assert store.get("op_other") is None
