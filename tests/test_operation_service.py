"""Operation lifecycles survive reconnects without replaying generation."""

import asyncio

from src.domain import (
    ArtifactKind, ArtifactResultData, ArtifactState, DomainErrorCode, DomainResult,
    LongOperationData, OperationState,
)
from src.infrastructure.state_store import RETENTION_SECONDS, CleanupJobRecord, OperationRecord, StateStore
from src.services.artifacts import artifact_from_local_path
from src.services.operations import OperationService


def _service(tmp_path, *, clock=lambda: 100, scope=lambda: "scope_a"):
    return OperationService(StateStore(tmp_path / "state.sqlite3", clock=clock), scope_provider=scope)


def _local_result(tmp_path, cid="c_owned"):
    path = tmp_path / "report.md"
    path.write_text("A verified report.")
    artifact = artifact_from_local_path(ArtifactKind.REPORT, str(path), source_chat_id=cid)
    return DomainResult.success(ArtifactResultData(ArtifactState.LOCAL, (artifact,), source_chat_id=cid),
                                verification_status="report_observed")


def test_start_returns_handle_before_slow_upstream_and_duplicate_key_runs_once(tmp_path):
    async def run():
        service = _service(tmp_path)
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def runner(context):
            calls.append(context.operation_id)
            context.observe_chat_sync("c_owned")
            entered.set()
            await finish.wait()
            return _local_result(tmp_path)
        first = await service.start("research", runner, idempotency_key="caller_token")
        assert first.data.state == OperationState.ACCEPTED
        assert not entered.is_set()
        await entered.wait()
        duplicate = await _service(tmp_path).start("research", runner, idempotency_key="caller_token")
        assert duplicate.data.operation_id == first.data.operation_id
        assert calls == [first.data.operation_id]
        finish.set()
        await service._tasks[first.data.operation_id]
        assert (await service.result(first.data.operation_id)).data.state == OperationState.COMPLETED
    asyncio.run(run())


def test_restart_recovers_failed_source_and_artifact_identity_without_generation(tmp_path):
    async def run():
        service = _service(tmp_path)
        async def interrupted(context):
            context.observe_chat_sync("c_owned")
            return DomainResult.failure(DomainErrorCode.UPSTREAM_REJECTED, "private raw provider message",
                                        data=ArtifactResultData(ArtifactState.FAILED, source_chat_id="c_owned"))
        started = await service.start("research", interrupted, output_dir=str(tmp_path), retain_chat=False, delete_after_seconds=90)
        await service._tasks[started.data.operation_id]
        recovered = _service(tmp_path)
        reads = []
        async def readback(record):
            reads.append(record)
            assert record.output_dir == str(tmp_path)
            assert record.retain_chat is False
            assert record.delete_after_seconds == 90
            return _local_result(tmp_path)
        recovered.register_recovery("research", readback)
        result = await recovered.result(started.data.operation_id)
        assert result.data.state == OperationState.COMPLETED
        assert result.data.operation_id == started.data.operation_id
        assert result.data.upstream_chat_id == "c_owned"
        artifact_id = result.data.artifacts[0].id
        assert (await _service(tmp_path).result(started.data.operation_id)).data.artifacts[0].id == artifact_id
        assert len(reads) == 1
        assert b"private raw provider message" not in service.store.path.read_bytes()
    asyncio.run(run())


def test_process_interruption_preserves_observed_source_and_safe_unknown_state(tmp_path):
    async def run():
        service = _service(tmp_path)
        entered = asyncio.Event()
        async def runner(context):
            context.observe_chat_sync("c_owned")
            entered.set()
            await asyncio.Event().wait()
        started = await service.start("video", runner)
        await entered.wait()
        task = service._tasks[started.data.operation_id]
        task.cancel()
        await task
        state = await _service(tmp_path).status(started.data.operation_id)
        assert state.meta.operation_state == OperationState.TIMED_OUT
        assert state.data.upstream_chat_id == "c_owned"
        assert state.data.cancellation_confirmed is False
    asyncio.run(run())


def test_two_clients_recover_once_and_cancelled_waiter_does_not_cancel_owner(tmp_path):
    async def run():
        first, second = _service(tmp_path), _service(tmp_path)
        initial, context, _ = first.reserve("research")
        context.observe_chat_sync("c_owned")
        first.finish(context, DomainResult.success(LongOperationData("research", OperationState.QUEUED,
                                                                     upstream_chat_id="c_owned"), operation_state=OperationState.QUEUED))
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def recovery(record):
            calls.append(record.operation_id)
            entered.set()
            await finish.wait()
            return _local_result(tmp_path)
        for service in (first, second):
            service.register_recovery("research", recovery)
        waiter = asyncio.create_task(first.result(initial.data.operation_id))
        await entered.wait()
        same_process = asyncio.create_task(first.result(initial.data.operation_id))
        await asyncio.sleep(0)
        other_client = await second.result(initial.data.operation_id)
        assert other_client.data.state == OperationState.QUEUED
        assert calls == [initial.data.operation_id]
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        finish.set()
        assert (await same_process).data.state == OperationState.COMPLETED
        assert (await second.result(initial.data.operation_id)).data.state == OperationState.COMPLETED
        assert calls == [initial.data.operation_id]
    asyncio.run(run())


def test_cancel_is_idempotent_best_effort_and_does_not_delete_source(tmp_path):
    async def run():
        service = _service(tmp_path)
        initial, context, _ = service.reserve("video")
        context.observe_chat_sync("c_owned")
        cancelled = await service.cancel(initial.data.operation_id)
        assert cancelled.ok
        assert cancelled.data.state == OperationState.CANCEL_REQUESTED
        assert cancelled.data.cancellation_confirmed is False
        assert cancelled.data.upstream_chat_id == "c_owned"
        assert (await service.cancel(initial.data.operation_id)).data.updated_at == cancelled.data.updated_at
        reads = []
        async def recovery(record):
            reads.append(record.operation_id)
            return DomainResult.success(ArtifactResultData(ArtifactState.QUEUED, source_chat_id="c_owned"), operation_state=OperationState.QUEUED)
        service.register_recovery("video", recovery)
        assert (await service.result(initial.data.operation_id)).data.state == OperationState.CANCEL_REQUESTED
        assert reads == [initial.data.operation_id]
    asyncio.run(run())


def test_provider_cancellation_requires_positive_boolean_confirmation(tmp_path):
    async def run():
        service = _service(tmp_path)
        initial, context, _ = service.reserve("video")
        context.observe_chat_sync("c_owned")
        async def recovery(record):
            raise AssertionError("cancel does not request a result")
        async def cancel(record):
            return True
        service.register_recovery("video", recovery, cancel=cancel)
        cancelled = await service.cancel(initial.data.operation_id)
        assert cancelled.data.state == OperationState.CANCELLED
        assert cancelled.data.cancellation_confirmed is True
        assert cancelled.meta.verification_status == "provider_cancelled"
    asyncio.run(run())


def test_cancel_before_worker_starts_avoids_provider_side_effect_without_claiming_provider_cancel(tmp_path):
    async def run():
        service = _service(tmp_path)
        calls = []
        async def runner(context):
            calls.append(context.operation_id)
            return _local_result(tmp_path)
        started = await service.start("research", runner)
        task = service._tasks[started.data.operation_id]
        requested = await service.cancel(started.data.operation_id)
        assert requested.data.state == OperationState.CANCEL_REQUESTED
        await task
        result = await service.status(started.data.operation_id)
        assert result.data.state == OperationState.CANCELLED
        assert result.data.cancellation_confirmed is False
        assert result.meta.verification_status == "local_cancelled_before_start"
        assert calls == []
    asyncio.run(run())


def test_account_switch_hides_old_handles_and_late_observation_stays_in_old_scope(tmp_path):
    async def run():
        scope = ["scope_a"]
        service = _service(tmp_path, scope=lambda: scope[0])
        initial, context, _ = service.reserve("research")
        scope[0] = "scope_b"
        context.observe_chat_sync("c_old_owned")
        assert (await service.result(initial.data.operation_id)).error.code == DomainErrorCode.OPERATION_NOT_FOUND
        assert service.store.operations.get("scope_a", initial.data.operation_id).upstream_chat_id == "c_old_owned"
        assert service.store.operations.get("scope_b", initial.data.operation_id) is None
        scope[0] = None
        assert (await service.status(initial.data.operation_id)).error.code == DomainErrorCode.AUTH_REQUIRED
    asyncio.run(run())


def test_no_source_after_restart_does_not_automatically_retry_generation(tmp_path):
    async def run():
        service = _service(tmp_path)
        initial, _context, _ = service.reserve("music")
        other = _service(tmp_path)
        async def recovery(record):
            raise AssertionError("no recorded source can be read")
        other.register_recovery("music", recovery)
        state = await other.result(initial.data.operation_id)
        assert state.data.state == OperationState.ACCEPTED
        assert state.data.continuation_possible is False
    asyncio.run(run())


def test_expired_handle_cannot_recover_or_cancel_and_can_be_pruned(tmp_path):
    async def run():
        now = [100]
        service = _service(tmp_path, clock=lambda: now[0])
        initial, _context, _ = service.reserve("research")
        now[0] += RETENTION_SECONDS
        # The next periodic maintenance physically removes old identifiers.
        assert (await service.status(initial.data.operation_id)).error.code == DomainErrorCode.OPERATION_NOT_FOUND
        # A record that expires between periodic passes still has an explicit
        # expired state until the next prune.
        service.store.operations.create(OperationRecord("op_expired", "scope_a", "research", OperationState.TIMED_OUT,
                                                         100, 100, now[0] - 1))
        for action in (service.status, service.result, service.cancel):
            result = await action("op_expired")
            assert result.error.code == DomainErrorCode.OPERATION_EXPIRED
            assert result.data.state == OperationState.EXPIRED
        assert service.store.operations.prune() == 1
        assert (await service.status(initial.data.operation_id)).error.code == DomainErrorCode.OPERATION_NOT_FOUND
    asyncio.run(run())


def test_late_failure_cas_cannot_overwrite_another_clients_completed_result(tmp_path, monkeypatch):
    service = _service(tmp_path)
    initial, context, _ = service.reserve("research")
    context.observe_chat_sync("c_owned")
    stale_update = service.store.operations.update
    injected = []
    def interleaved_update(scope, operation_id, **changes):
        if not injected:
            injected.append(True)
            other = _service(tmp_path)
            other._save_result(scope, operation_id, _local_result(tmp_path))
        return stale_update(scope, operation_id, **changes)
    monkeypatch.setattr(service.store.operations, "update", interleaved_update)
    failure = DomainResult.failure(DomainErrorCode.UPSTREAM_REJECTED, "late rejected read")
    result = service.finish(context, failure)
    assert result.ok
    assert result.data.state == OperationState.COMPLETED
    assert result.data.artifacts[0].state == ArtifactState.LOCAL


def test_provider_completed_without_verified_artifact_stays_recoverable(tmp_path):
    service = _service(tmp_path)
    initial, context, _ = service.reserve("music")
    context.observe_chat_sync("c_owned")
    result = service.finish(context, DomainResult.success(ArtifactResultData(ArtifactState.REMOTE, source_chat_id="c_owned")))
    assert result.data.state == OperationState.RUNNING
    assert result.data.continuation_possible


def test_expired_recovery_lease_is_safe_to_resume_and_old_worker_cannot_finish(tmp_path):
    service = _service(tmp_path)
    initial, context, _ = service.reserve("research")
    context.observe_chat_sync("c_owned")
    first = service.store.operations.claim(context.scope_id, context.operation_id, lease_id="lease_old", lease_until=101, now=100)
    second = service.store.operations.claim(context.scope_id, context.operation_id, lease_id="lease_new", lease_until=120, now=102)
    assert first is not None and second is not None
    assert service.store.operations.update(context.scope_id, context.operation_id, lease_id="lease_old", state=OperationState.FAILED) is None
    assert service.store.operations.renew(context.scope_id, context.operation_id, lease_id="lease_old", lease_until=140) is False
    service.store.operations.release(context.scope_id, context.operation_id, lease_id="lease_old")
    assert service.store.operations.get(context.scope_id, context.operation_id).lease_id == "lease_new"


def test_operation_result_joins_scoped_cleanup_receipt_without_private_lease_metadata(tmp_path):
    async def run():
        service = _service(tmp_path)
        initial, context, _ = service.reserve("video")
        context.observe_chat_sync("c_owned")
        service.finish(context, _local_result(tmp_path))
        other_scope = CleanupJobRecord("job_foreign", "scope_b", "c_owned", "failed", 100, 100, 100, 100 + RETENTION_SECONDS)
        service.store.cleanup_jobs.upsert(other_scope)
        assert "cleanup" not in (await service.result(initial.data.operation_id)).meta.details
        receipt = CleanupJobRecord("job_owned", "scope_a", "c_owned", "pending", 100, 100, 100, 100 + RETENTION_SECONDS)
        service.store.cleanup_jobs.upsert(receipt)
        pending = await service.result(initial.data.operation_id)
        assert pending.data.state == OperationState.COMPLETED
        assert pending.meta.details["cleanup"]["state"] == "pending"
        assert set(pending.meta.details["cleanup"]) == {"state", "verification_status", "job_id", "attempts", "diagnostic_id"}
        service.store.cleanup_jobs.update("scope_a", "c_owned", state="completed", verification_status="verified_absent")
        completed = await _service(tmp_path).result(initial.data.operation_id)
        assert completed.data.state == OperationState.COMPLETED
        assert completed.meta.details["cleanup"]["state"] == "completed"
        assert completed.meta.details["cleanup"]["verification_status"] == "verified_absent"
        assert completed.meta.details["cleanup"]["job_id"] == "job_owned"
        # Delete a receipt after its retention window: never borrow the same
        # CID's observation from another account as a replacement.
        service.store.cleanup_jobs.delete("scope_a", "c_owned")
        assert "cleanup" not in (await service.status(initial.data.operation_id)).meta.details
    asyncio.run(run())


def test_source_observation_retries_cas_without_losing_concurrent_cancel(tmp_path, monkeypatch):
    service = _service(tmp_path)
    initial, context, _ = service.reserve("video")
    original = service.store.operations.update
    injected = []
    def cancel_between_read_and_observe(scope, operation_id, **changes):
        if not injected:
            injected.append(True)
            original(scope, operation_id, state=OperationState.CANCEL_REQUESTED)
        return original(scope, operation_id, **changes)
    monkeypatch.setattr(service.store.operations, "update", cancel_between_read_and_observe)
    context.observe_chat_sync("c_owned")
    record = service.store.operations.get("scope_a", initial.data.operation_id)
    assert record.upstream_chat_id == "c_owned"
    assert record.state == OperationState.CANCEL_REQUESTED
