from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import AsyncMock

from PIL import Image
import pytest

from src.domain import ArtifactKind, CleanupObservation, CleanupState
from src.infrastructure.operation_store import OperationStore, RETENTION_SECONDS
from src.services import background
from src.services.artifacts import artifact_from_local_path
from src.services.operations import OperationService


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "state" / "operations.sqlite3"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(path))
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite3"))
    monkeypatch.setenv("GEMINI_PSID", "synthetic-background-account")
    monkeypatch.setenv("GEMINI_BROWSER_AUTO_LOAD", "false")
    monkeypatch.setenv("GEMINI_AUTO_REFRESH", "false")
    return OperationStore(path)


@pytest.fixture(autouse=True)
def _synthetic_account(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-offline-test-account")


def request(tmp_path):
    return {"prompt": "PRIVATE_PROMPT_MUST_NEVER_BE_STORED", "media_type": "image", "model": "flash",
            "thinking_level": "standard", "output_dir": str(tmp_path / "outputs")}


def test_submission_returns_handle_without_initializing_or_waiting_for_gemini(store, tmp_path, monkeypatch):
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 12345)
    result = asyncio.run(background.submit_media(request(tmp_path), operation="gemini_generate_media"))
    assert result.ok and result.data.state.value == "accepted"
    record = store.get(result.data.operation_id)
    assert record.worker_pid == 12345 and record.cleanup_protected
    assert dispatched[0]["request"]["prompt"] == request(tmp_path)["prompt"]
    assert request(tmp_path)["prompt"].encode() not in store.path.read_bytes()
    assert set(store.get_media_artifacts(record.operation_id)) == set()


def test_task_option_without_its_original_card_never_creates_or_dispatches_a_task(store, monkeypatch):
    launch = AsyncMock(side_effect=AssertionError('must not dispatch'))
    monkeypatch.setattr(background, '_dispatch', launch)
    handle = 'op_' + 'a' * 32
    result = asyncio.run(background.submit_task(
        {'prompt': 'synthetic-allow', 'input_option_id': 'option_' + 'a' * 32}, operation_id=handle))
    assert not result.ok and result.data is None
    launch.assert_not_awaited()
    assert store.get(handle) is None


def test_reusing_a_handle_never_resubmits_or_replaces_original_configuration(store, tmp_path, monkeypatch):
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 1)
    handle = "op_" + "a" * 32
    first = asyncio.run(background.submit_media(request(tmp_path), operation="media", operation_id=handle))
    second = asyncio.run(background.submit_media({**request(tmp_path), "prompt": "different", "model": "thinking"},
                                                 operation="media", operation_id=handle))
    assert first.ok and second.ok and len(dispatched) == 1
    assert store.get(handle).requested_model == "flash"


def test_concurrent_starts_claim_only_one_detached_worker(store, tmp_path, monkeypatch):
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 1)
    async def start():
        return await asyncio.gather(*(background.submit_media(request(tmp_path), operation="media",
                                      operation_id="op_" + "b" * 32) for _ in range(5)))
    assert all(result.ok for result in asyncio.run(start()))
    assert len(dispatched) == 1


def test_worker_start_failure_is_not_provider_acceptance(store, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("test")
    monkeypatch.setattr(background, "_launch_detached_worker", fail)
    handle = "op_" + "c" * 32
    result = asyncio.run(background.submit_media(request(tmp_path), operation="media", operation_id=handle))
    assert not result.ok and result.meta.verification_status == "worker_not_dispatched"
    record = store.get(handle)
    assert record.state == "failed" and not record.cleanup_protected and record.worker_token is None


def test_lease_takeover_rejects_old_worker_writes(tmp_path):
    now = [100.0]
    store = OperationStore(tmp_path / "op.sqlite", clock=lambda: now[0])
    store.create("job", "media")
    assert store.claim_worker("job", "original")
    assert not store.claim_worker("job", "duplicate")
    now[0] += 61
    assert store.claim_worker("job", "recovery")
    assert not store.checkpoint_media("job", worker_token="original", chat_id="c_old")
    assert not store.release_media_cleanup("job", worker_token="original")
    assert store.checkpoint_media("job", worker_token="recovery", chat_id="c_new", response_id="r_new", candidate_id="rc_new")


def completed_media(store, tmp_path):
    store.create("job", "media", retain_chat=False)
    store.configure_media("job", "image", str(tmp_path), "generated", "flash", "image")
    assert store.claim_worker("job", "worker")
    assert store.checkpoint_media("job", worker_token="worker", chat_id="c_test", response_id="r_test", candidate_id="rc_test")
    path = tmp_path / "generated.png"
    Image.new("RGB", (8, 8), "blue").save(path)
    artifact = artifact_from_local_path(ArtifactKind.IMAGE, str(path), source_chat_id="c_test")
    assert store.save_media_artifacts("job", (artifact,), worker_token="worker", artifact_state="local")
    return artifact


def test_crash_after_saving_is_recovered_and_results_remain_repeatable_after_cleanup(store, tmp_path):
    artifact = completed_media(store, tmp_path)
    store.retire_worker("job", "worker")
    assert store.is_cleanup_protected("c_test")
    finalize = AsyncMock(return_value=CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_test"))
    initializer = AsyncMock()
    service = OperationService(OperationStore(store.path), lambda: None, initializer, finalize)
    first = asyncio.run(service.get_result("job"))
    second = asyncio.run(service.get_result("job"))
    assert first.ok and second.ok and first.data.artifacts[0].id == artifact.id == second.data.artifacts[0].id
    assert not store.is_cleanup_protected("c_test")
    assert first.data.cleanup.state == CleanupState.COMPLETED
    finalize.assert_awaited_once()
    initializer.assert_not_awaited()


def test_completed_media_status_is_read_only_and_changed_files_fail_verification(store, tmp_path):
    completed_media(store, tmp_path)
    finalize = AsyncMock()
    service = OperationService(store, lambda: None, AsyncMock(), finalize)
    status = asyncio.run(service.get_status("job"))
    assert status.ok and status.data.state.value == "completed"
    assert status.data.artifacts == () and store.is_cleanup_protected("c_test")
    finalize.assert_not_awaited()
    (tmp_path / "generated.png").write_bytes(b"altered")
    result = asyncio.run(service.get_result("job"))
    assert not result.ok and result.meta.verification_status == "local_artifact_unavailable"
    assert store.is_cleanup_protected("c_test")


def test_status_before_any_remote_identity_reports_worker_dispatch_without_network(store, tmp_path, monkeypatch):
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: 1)
    submitted = asyncio.run(background.submit_media(request(tmp_path), operation="media"))
    initializer = AsyncMock()
    status = asyncio.run(OperationService(store, lambda: None, initializer).get_status(submitted.data.operation_id))
    assert status.ok and status.data.worker_active and status.data.state.value == "accepted"
    initializer.assert_not_awaited()


def test_default_artifact_directory_survives_a_temporary_caller(store, tmp_path, monkeypatch):
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: 1)
    caller = tmp_path / "temporary"
    caller.mkdir()
    monkeypatch.chdir(caller)
    arguments = request(tmp_path)
    arguments.pop("output_dir")
    result = asyncio.run(background.submit_media(arguments, operation="media"))
    record = store.get(result.data.operation_id)
    assert Path(record.output_dir).is_relative_to(store.path.parent)
    assert not Path(record.output_dir).is_relative_to(caller)
    caller.rmdir()


def test_failed_background_job_returns_typed_failure_and_cleanup_evidence(store, tmp_path, monkeypatch):
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: 1)
    handle = "op_" + "e" * 32
    asyncio.run(background.submit_media(request(tmp_path), operation="media", operation_id=handle))
    record = store.get(handle)
    store.finish_worker(handle, worker_token=record.worker_token, state="failed", phase="media_rejected",
                        verification_status="upstream_limit_message_observed", error_code="UPSTREAM_LIMIT_EXCEEDED")
    store.update_cleanup(handle, "completed")
    result = asyncio.run(OperationService(store, lambda: None, AsyncMock()).get_status(handle))
    assert not result.ok and result.error.code.value == "RATE_LIMITED"
    assert result.data.state.value == "failed" and result.data.error_code == "UPSTREAM_LIMIT_EXCEEDED"
    assert result.data.cleanup.state.value == "completed"
    repeated = asyncio.run(background.submit_media(request(tmp_path), operation="media", operation_id=handle))
    assert not repeated.ok and repeated.error.code.value == "RATE_LIMITED"


def test_complete_authentication_is_transferred_only_in_the_anonymous_pipe(store, tmp_path, monkeypatch):
    cookie = "SYNTHETIC_COOKIE_NOT_FOR_ARGV_OR_SQLITE"
    writes = []
    class Input:
        def write(self, value):
            writes.append(value)
        def close(self):
            pass
    class Process:
        pid = 12345
        stdin = Input()
        def wait(self):
            return 0
    def popen(args, **kwargs):
        assert cookie not in repr(args)
        assert kwargs["stdin"] == subprocess.PIPE and kwargs["stdout"] == subprocess.DEVNULL
        return Process()
    monkeypatch.setattr("src.client_manager.get_extra_cookies", lambda: {"__Secure-1PSID": cookie})
    monkeypatch.setattr(background.subprocess, "Popen", popen)
    result = asyncio.run(background.submit_media(request(tmp_path), operation="media"))
    assert result.ok and cookie.encode() in writes[0]
    assert cookie.encode() not in store.path.read_bytes()


def test_expiry_removes_artifact_locators_but_preserves_user_output_files(store, tmp_path):
    now = [1000.0]
    store.clock = lambda: now[0]
    artifact = completed_media(store, tmp_path)
    assert store.release_media_cleanup("job", worker_token="worker")
    now[0] += RETENTION_SECONDS + 1
    store.create("next", "gemini_research")
    assert store.get("job") is None and store.get_media_artifacts("job") == ()
    assert Path(artifact.local_path).is_file()


def test_repeated_results_do_not_extend_an_existing_cleanup_delay(store, tmp_path):
    completed_media(store, tmp_path)
    store.release_media_cleanup("job", worker_token="worker")
    store.update_cleanup("job", "pending")
    finalize = AsyncMock()
    service = OperationService(store, lambda: None, AsyncMock(), finalize)
    for _ in range(2):
        result = asyncio.run(service.get_result("job"))
        assert result.ok and result.data.cleanup.state == CleanupState.PENDING
    finalize.assert_not_awaited()


def test_worker_keeps_driving_delayed_cleanup_after_media_is_saved(store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src import client_wrapper, operation_worker

    completed_media(store, tmp_path)
    store.release_media_cleanup("job", worker_token="worker")
    store.update_cleanup("job", "pending")
    pending = CleanupObservation(state=CleanupState.PENDING, upstream_chat_id="c_test", delete_at=time.time())
    completed = CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id="c_test")
    observations = iter((pending, completed))
    monkeypatch.setattr(client_wrapper, "_cleanup_manager", SimpleNamespace(get_cleanup_observation=lambda cid: next(observations)))
    drain = AsyncMock()
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", drain)
    asyncio.run(operation_worker._wait_for_media_cleanup(store, "job", "worker", observation_seconds=1, poll_seconds=0.01))
    assert store.get("job").cleanup_state == "completed"
    drain.assert_awaited_once()


def test_research_worker_keeps_observing_a_late_plan_without_replaying_query(store, monkeypatch):
    from types import SimpleNamespace
    from src import client_wrapper, operation_worker

    # Keep this offline worker test away from process-global live client and cleanup state.
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: None)
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(reset_async=AsyncMock()))
    store.create("research", "gemini_research", requested_model="thinking", thinking_level="extended")
    assert store.claim_worker("research", "worker")
    calls = []
    async def research(request, operation_id, token, *, resume):
        calls.append((dict(request), resume))
        if not resume:
            store.update(operation_id, state="partial", phase="plan_outcome_unverified", upstream_chat_id="c_late")
        else:
            assert request == {}
            store.update(operation_id, state="running", phase="confirmation_returned")
    monkeypatch.setattr(operation_worker, "_research", research)
    monkeypatch.setattr("src.services.operations.OperationService.get_result", AsyncMock())
    asyncio.run(operation_worker.run_worker(
        {"kind": "research", "operation_id": "research", "worker_token": "worker", "request": {"query": "original"}},
        store=store, observation_seconds=1, poll_seconds=0.001,
    ))
    assert calls == [({"query": "original"}, False), ({}, True)]
    assert store.get("research").phase == "confirmation_returned" and store.get("research").worker_token is None


def test_detached_worker_survives_call_loop_and_deleted_caller_directory(store, tmp_path, monkeypatch):
    bootstrap = '''
import asyncio, json
from pathlib import Path
from PIL import Image
from src import client_wrapper, operation_worker
from src.services import media_jobs
from types import SimpleNamespace
from src.services.artifacts import artifact_from_local_path
from src.domain import ArtifactKind
async def noop(*args, **kwargs):
    return None
client_wrapper.init_startup_infrastructure = lambda: None
client_wrapper.initialize_client = noop
client_wrapper._drain_due_cleanup = noop
client_wrapper._client_manager = SimpleNamespace(reset_async=noop)
async def generate(request, *, operation_id, store, worker_token):
    await asyncio.sleep(0.3)
    path = Path(request['output_dir']) / 'worker.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (8,8), 'blue').save(path)
    store.checkpoint_media(operation_id,worker_token=worker_token,chat_id='c_worker',response_id='r_worker',candidate_id='rc_worker')
    artifact = artifact_from_local_path(ArtifactKind.IMAGE,str(path),source_chat_id='c_worker')
    store.save_media_artifacts(operation_id,(artifact,),worker_token=worker_token,artifact_state='local')
    store.release_media_cleanup(operation_id,worker_token=worker_token)
    store.update_cleanup(operation_id,'completed')
media_jobs.execute_media_job = generate
asyncio.run(operation_worker.run_worker(json.load(__import__('sys').stdin)))
'''
    real_popen = subprocess.Popen
    launched = []
    def popen(args, **kwargs):
        assert request(tmp_path)["prompt"] not in repr(args)
        assert kwargs["start_new_session"] and Path(kwargs["cwd"]).is_dir()
        process = real_popen([sys.executable, "-c", bootstrap], **kwargs)
        launched.append(process)
        return process
    monkeypatch.setattr(background.subprocess, "Popen", popen)
    monkeypatch.setattr("src.client_manager.get_extra_cookies", lambda: {})
    caller = tmp_path / "temporary-caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    submitted = asyncio.run(background.submit_media(request(tmp_path), operation="media"))
    caller.rmdir()
    assert submitted.ok
    deadline = time.monotonic() + 15
    try:
        while time.monotonic() < deadline:
            record = store.get(submitted.data.operation_id)
            if record.state == "completed" and record.worker_token is None:
                break
            time.sleep(0.05)
        assert record.state == "completed" and record.worker_token is None
        assert request(tmp_path)["prompt"].encode() not in store.path.read_bytes()
        assert store.get_media_artifacts(record.operation_id)[0]["size_bytes"] > 0
    finally:
        for process in launched:
            process.wait(timeout=10)


def test_worker_paths_are_absolute_before_changing_cwd(store, tmp_path, monkeypatch):
    import io

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", "relative/cleanup.sqlite")
    monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
    monkeypatch.setenv("GEMINI_COOKIE_PATH", "relative/cookie-cache")
    captured = {}

    class Process:
        pid = 321
        stdin = io.BytesIO()

        def wait(self):
            return 0

    def popen(*args, **kwargs):
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(background.subprocess, "Popen", popen)
    monkeypatch.setattr("src.client_manager.get_extra_cookies", lambda: {})
    background._launch_detached_worker({"kind": "media"}, store)
    env = captured["env"]
    assert env["GEMINI_OPERATION_DB_PATH"] == str(store.path.resolve())
    assert env["GEMINI_CLEANUP_DB_PATH"] == str((tmp_path / "relative/cleanup.sqlite").resolve())
    assert env["XDG_STATE_HOME"] == str((tmp_path / "relative/state").resolve())
    assert env["GEMINI_COOKIE_PATH"] == str((tmp_path / "relative/cookie-cache").resolve())
    assert captured["cwd"] == store.path.resolve().parent


def test_worker_without_identifiable_account_never_generates(store, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from src import client_wrapper, operation_worker
    from src.services import media_jobs

    monkeypatch.delenv("GEMINI_PSID", raising=False)
    store.create("no_account", "media")
    assert store.claim_worker("no_account", "worker")
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: None)
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(reset_async=AsyncMock()))
    generate = AsyncMock()
    monkeypatch.setattr(media_jobs, "execute_media_job", generate)
    asyncio.run(operation_worker.run_worker(
        {"operation_id": "no_account", "worker_token": "worker", "kind": "media", "request": request(tmp_path)},
        store=store, observation_seconds=0.01, poll_seconds=0.01,
    ))
    generate.assert_not_awaited()
    record = store.get("no_account")
    assert record.phase == "account_identity_unavailable" and record.cleanup_protected


def test_missing_operation_queue_entries_reconciled_only_for_same_account(store, monkeypatch):
    from src.infrastructure.account_identity import current_account_key

    store.create("protected", "research")
    store.update("protected", state="running", phase="start_returned", upstream_chat_id="c_protected")
    store.create("retained", "research", retain_chat=True)
    store.update("retained", state="running", phase="start_returned", upstream_chat_id="c_retained")
    store.create("other", "research")
    store.update("other", state="running", phase="start_returned", upstream_chat_id="c_other")
    import sqlite3
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE operations SET account_key=? WHERE operation_id='other'", ("f" * 64,))
    assert current_account_key()
    jobs = []
    def schedule(cid, **kwargs):
        jobs.append((cid, kwargs))
        return CleanupObservation(state=CleanupState.RETAINED if kwargs["retain_chat"] else CleanupState.PENDING,
                                  upstream_chat_id=cid)
    assert background.repair_operation_cleanup(store, lambda cid: None, schedule) == 2
    assert {cid for cid, _ in jobs} == {"c_protected", "c_retained"}
    assert next(opts for cid, opts in jobs if cid == "c_protected")["delete_after_seconds"] >= 1800
    assert next(opts for cid, opts in jobs if cid == "c_retained")["retain_chat"]
    assert store.get("protected").cleanup_protected


@pytest.mark.parametrize("notebook_chat", [False, True])
def test_workflow_submission_deduplicates_original_handle_without_storing_prompt(store, monkeypatch, notebook_chat):
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 1)
    handle = "op_" + "a" * 32
    request = {"prompt": "PRIVATE_NOTEBOOK_TASK_PROMPT", "notebook_ids": ["notebooks/id"]}
    result = asyncio.run(background.submit_task(request, operation_id=handle, notebook_chat=notebook_chat))
    duplicate = asyncio.run(background.submit_task({**request, "prompt": "different"}, operation_id=handle, notebook_chat=notebook_chat))
    assert result.ok and duplicate.ok and len(dispatched) == 1
    assert result.data.execution_mode == ("chat" if notebook_chat else "task")
    assert dispatched[0]["kind"] == ("notebook_chat" if notebook_chat else "task")
    assert request["prompt"].encode() not in store.path.read_bytes()
    other = asyncio.run(background.submit_task(request, operation_id=handle, notebook_chat=not notebook_chat))
    assert not other.ok and len(dispatched) == 1


@pytest.mark.parametrize("operation", ["gemini_run_task", "gemini_notebook_chat"])
@pytest.mark.parametrize("state", ["partial", "completed"])
@pytest.mark.parametrize("candidate_id", [None, "rc_one"])
def test_interrupted_workflow_resumes_readback_with_no_prompt(store, monkeypatch, operation, state, candidate_id):
    store.create("op_" + "b" * 32, operation)
    assert store.claim_worker("op_" + "b" * 32, "old")
    store.checkpoint_media("op_" + "b" * 32, worker_token="old", chat_id="c_one", response_id="r_one", candidate_id=candidate_id)
    store.finish_worker("op_" + "b" * 32, worker_token="old", state=state, phase="reply_observed", verification_status="verified")
    store.retire_worker("op_" + "b" * 32, "old")
    store.clock = lambda: time.time() + 65
    dispatched = []
    monkeypatch.setattr(background, "OperationStore", lambda: store)
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 1)
    asyncio.run(background.maintain_background_operations())
    assert len(dispatched) == 1 and dispatched[0]["kind"] == "task_resume" and dispatched[0]["request"] == {}


def test_completed_workflow_worker_saves_answer_without_caller_result_read(store, monkeypatch):
    from types import SimpleNamespace
    from src import client_wrapper, operation_worker
    from src.services import task_jobs
    handle = "op_" + "c" * 32
    store.create(handle, "gemini_run_task")
    assert store.claim_worker(handle, "worker")
    monkeypatch.setattr(client_wrapper, "init_startup_infrastructure", lambda: None)
    monkeypatch.setattr(client_wrapper, "_drain_due_cleanup", AsyncMock())
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(reset_async=AsyncMock()))
    async def execute(request, **kwargs):
        store.checkpoint_media(handle, worker_token="worker", chat_id="c_one", response_id="r_one", candidate_id="rc_one")
        store.finish_worker(handle, worker_token="worker", state="completed", phase="task_completed", verification_status="verified")
    async def result(self, operation_id):
        assert store.get(operation_id).cleanup_protected
        store.release_cleanup(operation_id)
        store.update_cleanup(operation_id, "completed")
    saved = AsyncMock(side_effect=result)
    monkeypatch.setattr(task_jobs, "execute_task_job", execute)
    monkeypatch.setattr(OperationService, "get_result", lambda self, operation_id: saved(self, operation_id))
    asyncio.run(operation_worker.run_worker(
        {"kind": "task", "operation_id": handle, "worker_token": "worker", "request": {"prompt": "private"}},
        store=store, observation_seconds=1, poll_seconds=0.001))
    saved.assert_awaited_once()
    assert not store.get(handle).cleanup_protected and store.get(handle).cleanup_state == "completed"


@pytest.mark.parametrize("notebook_chat", [False, True])
@pytest.mark.parametrize("level", ["low", "medium", "high", "低", "中", "高"])
def test_workflow_preserves_three_level_thinking_for_worker_and_resume(store, monkeypatch, notebook_chat, level):
    dispatched = []
    monkeypatch.setattr(background, "_launch_detached_worker", lambda payload, store: dispatched.append(payload) or 1)
    request = {"prompt": "synthetic thinking probe", "notebook_ids": ["notebooks/synthetic"],
               "thinking_level": level}
    first = asyncio.run(background.submit_task(request, notebook_chat=notebook_chat))
    assert first.ok
    handle = first.data.operation_id
    original = store.get(handle)
    assert original.thinking_level == level
    assert dispatched[0]["request"]["thinking_level"] == level
    resumed = asyncio.run(background.submit_task({**request, "thinking_level": "high"},
                                                 operation_id=handle, notebook_chat=notebook_chat))
    assert resumed.ok and len(dispatched) == 1
    assert store.get(handle).thinking_level == level
