"""Cleanup guards release only for a recorded failure owned by the current worker."""

import pytest

from src.infrastructure.operation_store import OperationStore
from src.services.task_result_storage import save_task_result


@pytest.mark.parametrize(("phase", "code", "released"), [
    ("media_rejected", "UPSTREAM_REJECTED", True),
    ("media_rejected", "ARTIFACT_NOT_RETURNED", True),
    ("media_rejected", "UPSTREAM_LIMIT_EXCEEDED", True),
    ("media_artifact_not_observed", "UPSTREAM_REJECTED", False),
    ("media_rejected", "READBACK_UNVERIFIED", False),
    ("media_rejected", None, False),
])
def test_rejection_cleanup_requires_recorded_failure_and_matching_worker(
    tmp_path, monkeypatch, phase, code, released,
):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-cleanup-failure-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create("job", "gemini_generate_music", retain_chat=False)
    store.configure_media("job", "music", str(tmp_path), "music", "pro", "music")
    assert store.claim_worker("job", "owner")
    assert store.checkpoint_media(
        "job", worker_token="owner", chat_id="c_test", response_id="r_test", candidate_id="rc_test",
    )
    assert store.finish_worker(
        "job", worker_token="owner", state="partial", phase=phase,
        error_code=code, verification_status="upstream_media_error_card_observed",
    )

    reopened = OperationStore(store.path)
    assert not reopened.release_failed_media_cleanup("job", worker_token="other")
    assert reopened.get("job").cleanup_protected
    assert reopened.release_failed_media_cleanup("job", worker_token="owner") is released
    assert reopened.get("job").cleanup_protected is not released


def test_research_observation_is_durable_before_worker_exit_and_enriches_same_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-research-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create("research", "gemini_research", requested_model="pro", thinking_level="extended")
    original = store.get("research")
    assert store.claim_worker("research", "original")
    assert store.attach_research_observation("research", "c_test", account_key=original.account_key)

    reopened = OperationStore(store.path)
    record = reopened.get("research")
    assert (record.state, record.phase, record.verification_status) == (
        "partial", "plan_outcome_unverified", "start_outcome_unverified",
    )
    assert record.cleanup_protected and record.worker_token == "original"
    assert reopened.attach_research_observation(
        "research", "c_test", account_key=original.account_key, response_id="r_test", candidate_id="rc_test",
    )
    record = reopened.get("research")
    assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
        "c_test", "r_test", "rc_test",
    )
    assert (record.requested_model, record.thinking_level) == ("pro", "extended")


@pytest.mark.parametrize(("cid", "rid", "rcid"), [
    ("c_other", "r_test", "rc_test"),
    ("c_test", "r_other", "rc_test"),
    ("c_test", "r_test", "rc_other"),
])
def test_research_observation_never_replaces_bound_identity(tmp_path, monkeypatch, cid, rid, rcid):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-research-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create("research", "gemini_research")
    key = store.get("research").account_key
    assert store.attach_research_observation(
        "research", "c_test", account_key=key, response_id="r_test", candidate_id="rc_test",
    )
    assert not store.attach_research_observation(
        "research", cid, account_key=key, response_id=rid, candidate_id=rcid,
    )
    record = store.get("research")
    assert (record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id) == (
        "c_test", "r_test", "rc_test",
    )


@pytest.mark.parametrize("phase", ["confirmation_requested", "research_running_observed", "report_observed"])
def test_late_research_observation_keeps_later_phase_and_original_account(tmp_path, monkeypatch, phase):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-research-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.create("research", "gemini_research")
    key = store.get("research").account_key
    store.update("research", state="running", phase=phase, verification_status="existing_evidence")
    monkeypatch.setenv("GEMINI_PSID", "another-synthetic-account")

    assert not store.attach_research_observation("research", "c_test", account_key="wrong-account")
    assert store.attach_research_observation("research", "c_test", account_key=key)
    record = store.get("research")
    assert (record.state, record.phase, record.verification_status) == ("running", phase, "existing_evidence")
    assert record.account_key == key and record.cleanup_protected
    store.release_cleanup("research")
    assert not store.attach_research_observation("research", "c_test", account_key=key)


@pytest.mark.parametrize(("terminal_state", "late_state"), [
    ("failed", "partial"),
    ("failed", "completed"),
    ("completed", "partial"),
    ("completed", "failed"),
    ("failed", "failed"),
    ("completed", "completed"),
])
def test_late_worker_finish_cannot_regress_a_saved_task_terminal(tmp_path, monkeypatch, terminal_state, late_state):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-terminal-task-account")
    store = OperationStore(tmp_path / "operations.sqlite3")
    handle = "op_" + "s" * 32
    store.create(handle, "gemini_run_task")
    assert store.claim_worker(handle, "owner")
    assert store.checkpoint_media(
        handle, worker_token="owner", chat_id="c_task", response_id="r_task", candidate_id="rc_task",
    )
    artifact = save_task_result(store, store.get(handle), "The exact terminal Task response.")
    store.commit_task_files(handle, response_id='r_task', candidate_id='rc_task', artifacts=())
    assert store.release_task_result_cleanup(handle, terminal_state=terminal_state)
    store.update_cleanup(handle, "completed")
    before = store.get(handle)
    saved_artifacts = store.get_media_artifacts(handle)

    assert not store.finish_worker(
        handle, worker_token="owner", state=late_state, phase="late_worker_outcome",
        verification_status="not_observed", error_code="WORKER_INTERRUPTED",
    )

    reopened = OperationStore(store.path)
    after = reopened.get(handle)
    assert after == before
    assert (after.state, after.phase) == (
        terminal_state, "task_failure_saved" if terminal_state == "failed" else "task_answer_saved",
    )
    assert after.error_code == ("UPSTREAM_REJECTED" if terminal_state == "failed" else None)
    assert not after.cleanup_protected and after.cleanup_state == "completed"
    assert reopened.get_media_artifacts(handle) == saved_artifacts
    assert reopened.get_media_artifacts(handle)[0]["artifact_id"] == artifact.id


@pytest.mark.parametrize(("phase", "recoverable"), [
    ("task_failed", True),
    ("task_failure_saved", True),
    ("input_rejected", False),
    ("workflow_unavailable", False),
])
def test_only_retryable_failed_task_phases_can_be_discovered_and_claimed(tmp_path, monkeypatch, phase, recoverable):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-failed-task-account")
    now = [1000.0]
    store = OperationStore(tmp_path / "operations.sqlite3", clock=lambda: now[0])
    handle = "op_" + "f" * 32
    store.create(handle, "gemini_run_task", retain_chat=False)
    assert store.claim_worker(handle, "original")
    assert store.checkpoint_media(
        handle, worker_token="original", chat_id="c_task", response_id="r_task", candidate_id="rc_task",
    )
    assert store.finish_worker(
        handle, worker_token="original", state="failed", phase=phase,
        verification_status="observed_failure", error_code="UPSTREAM_REJECTED" if recoverable else "INVALID_ARGUMENT",
    )
    if phase == "task_failure_saved":
        save_task_result(store, store.get(handle), "Saved failed Task answer, schedule protection still unknown.")
        store.commit_task_files(handle, response_id='r_task', candidate_id='rc_task', artifacts=())
        assert store.checkpoint_task_result_state(handle, terminal_state="failed")
    store.retire_worker(handle, "original")
    now[0] += 61

    reopened = OperationStore(store.path, clock=lambda: now[0])
    discovered = {record.operation_id for record in reopened.recoverable_web_workflows()}
    assert (handle in discovered) is recoverable
    assert reopened.claim_worker(handle, "retry") is recoverable
    after = reopened.get(handle)
    assert after.state == "failed" and after.phase == phase and after.cleanup_protected
    assert after.worker_token == ("retry" if recoverable else None)
