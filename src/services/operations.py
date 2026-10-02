"""Restart-safe, content-free operation handles shared by every MCP surface."""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..domain import (
    Artifact, ArtifactState, ArtifactVerificationStatus, DomainErrorCode, DomainResult, LongOperationData, OperationState, new_operation_id,
)
from ..infrastructure.state_store import (
    RETENTION_SECONDS, ArtifactLocator, OperationRecord, StateStore, StateStoreError,
    get_default_state_store,
)

Runner = Callable[["OperationContext"], Awaitable[DomainResult[Any]]]
Recovery = Callable[[OperationRecord], Awaitable[DomainResult[Any]]]
ProviderCancel = Callable[[OperationRecord], Awaitable[bool]]
_TERMINAL = {OperationState.COMPLETED, OperationState.CANCELLED, OperationState.EXPIRED}
RECOVERY_TIMEOUT_SECONDS = 240
LEASE_SECONDS = 270
HEARTBEAT_SECONDS = 30
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _merge_locators(existing: tuple[ArtifactLocator, ...], incoming: tuple[ArtifactLocator, ...]) -> tuple[ArtifactLocator, ...]:
    locators = {item.id: item for item in existing}
    for item in incoming:
        old = locators.get(item.id)
        if old and old.local_path and old.verification == "verified" and not item.local_path:
            item = replace(old, uri=item.uri or old.uri)
        locators[item.id] = item
    return tuple(locators.values())


class OperationContext:
    """Request-owned locator observer; never carries persisted request content."""

    def __init__(self, service: OperationService, record: OperationRecord):
        self._service = service
        self.operation_id = record.operation_id
        self.scope_id = record.scope_id
        self._lease_id: str | None = record.lease_id

    def observe_chat_sync(self, cid: str, *, provider_operation_id: str | None = None) -> None:
        changes: dict[str, Any] = {"upstream_chat_id": cid}
        if provider_operation_id is not None:
            changes["provider_operation_id"] = provider_operation_id
        # Identity comes from start(), not whichever account is current when
        # an old SDK response arrives. Preserve that old locator without ever
        # granting the new account access to it.
        for _attempt in range(8):
            current = self._service.store.operations.get(self.scope_id, self.operation_id)
            if current is None:
                raise StateStoreError("Operation metadata disappeared before source retention.")
            if current.upstream_chat_id and current.upstream_chat_id != cid:
                raise ValueError("Observed operation source identity changed.")
            # Locator observation is monotonic, even if the original worker
            # releases its lease before a late SDK metadata callback arrives.
            # CAS protects competing state writes; this never runs a provider
            # action or replaces a different observed source.
            if self._service.store.operations.update(self.scope_id, self.operation_id, expected_version=current.version,
                                                     **changes) is not None:
                return
        raise StateStoreError("Operation source could not be retained.")

    def observe_provider_operation_sync(self, provider_operation_id: str) -> None:
        for _attempt in range(8):
            current = self._service.store.operations.get(self.scope_id, self.operation_id)
            if current is None:
                raise StateStoreError("Operation metadata disappeared before source retention.")
            if self._service.store.operations.update(self.scope_id, self.operation_id, expected_version=current.version,
                                                     provider_operation_id=provider_operation_id) is not None:
                return
        raise StateStoreError("Operation provider locator could not be retained.")

    async def observe_chat(self, cid: str, *, provider_operation_id: str | None = None) -> None:
        self.observe_chat_sync(cid, provider_operation_id=provider_operation_id)

    def observe_artifacts_sync(
        self, artifacts: tuple[Artifact, ...], *, operation_state: OperationState | None = None,
        verification_status: str | None = None,
    ) -> None:
        """Durably save availability before any source cleanup can run."""
        incoming = tuple(ArtifactLocator.from_artifact(item) for item in artifacts)
        for _attempt in range(8):
            current = self._service.store.operations.get(self.scope_id, self.operation_id)
            if current is None:
                raise StateStoreError("Operation metadata disappeared before artifact retention.")
            changes: dict[str, Any] = {"artifacts": _merge_locators(current.artifacts, incoming)}
            if operation_state is not None:
                if operation_state == OperationState.COMPLETED and current.operation_type in {"research", "music", "video"} and (
                    not changes["artifacts"] or any(item.state != "local" or item.verification != "verified" for item in changes["artifacts"])
                ):
                    changes["state"] = OperationState.PARTIAL if changes["artifacts"] else OperationState.RUNNING
                else:
                    changes["state"] = operation_state
            if verification_status is not None:
                changes["verification_status"] = verification_status
            if self._service.store.operations.update(self.scope_id, self.operation_id, expected_version=current.version,
                                                     lease_id=self._lease_id, **changes) is not None:
                return
        raise StateStoreError("Operation artifact retention lost its execution lease.")

    def observe_destination_sync(self, output_dir: str) -> None:
        current = self._service.store.operations.get(self.scope_id, self.operation_id)
        if current is not None:
            self._service.store.operations.update(self.scope_id, self.operation_id, expected_version=current.version,
                                                 lease_id=self._lease_id, output_dir=str(Path(output_dir).expanduser().absolute()))

    async def update(
        self, *, state: OperationState | None = None, artifacts: tuple[Artifact, ...] = (),
        verification_status: str | None = None,
    ) -> None:
        changes: dict[str, Any] = {}
        if state is not None:
            changes["state"] = state
        if artifacts:
            self.observe_artifacts_sync(artifacts, operation_state=state, verification_status=verification_status)
            return
        if verification_status is not None:
            changes["verification_status"] = verification_status
        if changes:
            current = self._service.store.operations.get(self.scope_id, self.operation_id)
            if current is not None:
                self._service.store.operations.update(self.scope_id, self.operation_id, expected_version=current.version,
                                                     lease_id=self._lease_id, **changes)


class OperationService:
    """Start once, recover by locator, and expose idempotent explicit handles.

    Runner closures exist only while this process lives. A different process
    can register the same recovery handler and read the existing locator; it
    cannot replay the original prompt because no prompt is stored.
    """

    def __init__(self, store: StateStore | None = None, scope_provider: Callable[[], str | None] | None = None):
        self._store = store
        self._scope_provider = scope_provider
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._refresh_tasks: dict[str, asyncio.Task[DomainResult[LongOperationData]]] = {}
        self._recovery: dict[str, Recovery] = {}
        self._cancellers: dict[str, ProviderCancel] = {}

    @property
    def store(self) -> StateStore:
        return self._store or get_default_state_store()

    def _scope(self) -> str | None:
        if self._scope_provider is not None:
            return self._scope_provider()
        # The client owner selects effective browser/manual credentials. A
        # raw environment value can refer to a different account, or be empty
        # while browser authentication is active.
        from ..client_wrapper import get_authentication_scope
        return get_authentication_scope()

    def register_recovery(self, kind: str, recovery: Recovery, *, cancel: ProviderCancel | None = None) -> None:
        self._recovery[kind] = recovery
        if cancel is not None:
            self._cancellers[kind] = cancel

    async def start(
        self, kind: str, runner: Runner, *, recovery: Recovery | None = None,
        idempotency_key: str | None = None, output_dir: str | None = None,
        retain_chat: bool = True, delete_after_seconds: int | None = None,
    ) -> DomainResult[LongOperationData]:
        if recovery is not None:
            self.register_recovery(kind, recovery)
        result, context, created = self.reserve(kind, idempotency_key=idempotency_key,
                                                output_dir=output_dir, retain_chat=retain_chat,
                                                delete_after_seconds=delete_after_seconds)
        if context is not None and created:
            record = self.store.operations.get(context.scope_id, context.operation_id)
            if record is not None:
                task = asyncio.create_task(self._run(record, runner))
                self._tasks[record.operation_id] = task
                task.add_done_callback(lambda _task: self._tasks.pop(record.operation_id, None))
        return result

    def reserve(
        self, kind: str, *, idempotency_key: str | None = None, output_dir: str | None = None,
        retain_chat: bool = True, delete_after_seconds: int | None = None,
    ) -> tuple[DomainResult[LongOperationData], OperationContext | None, bool]:
        """Allocate the same durable handle for a synchronous compatibility call."""
        if idempotency_key is not None and not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "idempotency_key must be an opaque ASCII token of at most 128 characters.",
                                        verification_status="input_rejected"), None, False
        scope = self._scope()
        if scope is None:
            return self._missing_auth(), None, False
        now = self.store.clock()
        try:
            record, created = self.store.operations.create(OperationRecord(
                operation_id=new_operation_id(), scope_id=scope, operation_type=kind,
                state=OperationState.ACCEPTED, created_at=now, updated_at=now,
                expires_at=now + RETENTION_SECONDS, idempotency_key=idempotency_key,
                output_dir=str(Path(output_dir).expanduser().absolute()) if output_dir else None,
                retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
            ))
        except (StateStoreError, ValueError):
            return self._storage_error(), None, False
        return self._render(record), OperationContext(self, record), created

    def finish(self, context: OperationContext, result: DomainResult[Any]) -> DomainResult[LongOperationData]:
        """Finish a request using its captured identity, including after an account switch."""
        try:
            record = self._save_result(context.scope_id, context.operation_id, result, lease_id=context._lease_id)
            return self._render(record) if record is not None else self._storage_error()
        except (StateStoreError, ValueError):
            return self._storage_error()

    @asynccontextmanager
    async def execution(self, context: OperationContext):
        """One lease for generation/start or recovery, including compat calls."""
        if context._lease_id is not None:
            yield True
            return
        lease_id = "lease_" + secrets.token_hex(16)
        claimed = self.store.operations.claim(context.scope_id, context.operation_id, lease_id=lease_id,
                                              lease_until=self.store.clock() + LEASE_SECONDS, now=self.store.clock())
        if claimed is None:
            yield False
            return
        if claimed.state == OperationState.ACCEPTED:
            started = self.store.operations.update(context.scope_id, context.operation_id, expected_version=claimed.version,
                                                   lease_id=lease_id, state=OperationState.RUNNING, attempt_count=1)
            if started is None:
                self.store.operations.release(context.scope_id, context.operation_id, lease_id=lease_id)
                yield False
                return
        if claimed.state == OperationState.CANCEL_REQUESTED and claimed.attempt_count == 0 and not (
            claimed.upstream_chat_id or claimed.provider_operation_id
        ):
            self.store.operations.update(context.scope_id, context.operation_id, expected_version=claimed.version,
                                         lease_id=lease_id, state=OperationState.CANCELLED,
                                         error_code=DomainErrorCode.CANCELLED.value,
                                         verification_status="local_cancelled_before_start")
            self.store.operations.release(context.scope_id, context.operation_id, lease_id=lease_id)
            yield False
            return
        context._lease_id = lease_id
        heartbeat = asyncio.create_task(self._heartbeat(context.scope_id, context.operation_id, lease_id))
        try:
            yield True
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.store.operations.release(context.scope_id, context.operation_id, lease_id=lease_id)

    async def _run(self, record: OperationRecord, runner: Runner) -> None:
        context = OperationContext(self, record)
        try:
            if self._scope() != record.scope_id:
                self.store.operations.update(record.scope_id, record.operation_id,
                                             state=OperationState.TIMED_OUT, verification_status="authentication_changed")
                return
            async with self.execution(context) as owns:
                if not owns:
                    return
                self.store.operations.update(record.scope_id, record.operation_id, lease_id=context._lease_id,
                                             state=OperationState.RUNNING, attempt_count=1)
                result = await runner(context)
                self.finish(context, result)
        except asyncio.CancelledError:
            # Process shutdown/local cancellation does not confirm a provider
            # cancellation. Persist locators and leave the work recoverable.
            current = self.store.operations.get(record.scope_id, record.operation_id)
            if current and current.state not in _TERMINAL:
                state = current.state if current.state == OperationState.CANCEL_REQUESTED else OperationState.TIMED_OUT
                self.store.operations.update(record.scope_id, record.operation_id, expected_version=current.version, state=state,
                                             error_code=DomainErrorCode.TIMED_OUT.value, verification_status="execution_interrupted")
        except Exception:
            # Exception text may include prompts or provider payloads. Persist
            # a stable code only; never serialize/log that text from this layer.
            current = self.store.operations.get(record.scope_id, record.operation_id)
            if current and current.state not in _TERMINAL:
                self.store.operations.update(record.scope_id, record.operation_id, expected_version=current.version,
                                             state=OperationState.FAILED, error_code=DomainErrorCode.INTERNAL_ERROR.value,
                                             verification_status="execution_failed")

    def _save_result(self, scope: str, operation_id: str, result: DomainResult[Any], *, lease_id: str | None = None) -> OperationRecord | None:
        current = self.store.operations.get(scope, operation_id)
        if current is None or current.state in _TERMINAL:
            return current
        data = result.data
        changes: dict[str, Any] = {"state": result.meta.operation_state,
                                  "verification_status": result.meta.verification_status,
                                  "error_code": result.error.code.value if result.error else None}
        chat_id = getattr(data, "upstream_chat_id", None) or getattr(data, "source_chat_id", None)
        provider_id = getattr(data, "upstream_operation_id", None)
        if chat_id and current.upstream_chat_id and current.upstream_chat_id != chat_id:
            updated = self.store.operations.update(scope, operation_id, expected_version=current.version, lease_id=lease_id,
                                                  state=OperationState.FAILED, error_code=DomainErrorCode.UPSTREAM_CHANGED.value,
                                                  verification_status="source_identity_changed")
            return updated or self.store.operations.get(scope, operation_id)
        if chat_id:
            changes["upstream_chat_id"] = chat_id
        if provider_id:
            changes["provider_operation_id"] = provider_id
        artifacts = tuple(getattr(data, "artifacts", ()) or ())
        if artifacts:
            changes["artifacts"] = _merge_locators(current.artifacts, tuple(ArtifactLocator.from_artifact(item) for item in artifacts))
        if changes["state"] == OperationState.COMPLETED and current.operation_type in {"research", "music", "video"}:
            available = changes.get("artifacts", current.artifacts)
            if not available or any(item.state != ArtifactState.LOCAL.value or item.verification != ArtifactVerificationStatus.VERIFIED.value for item in available):
                # A provider completion signal without a usable local result
                # must keep the recorded source eligible for read-back.
                changes["state"] = OperationState.PARTIAL if available else OperationState.RUNNING
                changes["verification_status"] = "completion_not_observed"
        updated = self.store.operations.update(scope, operation_id, expected_version=current.version, lease_id=lease_id, **changes)
        # Another client may have completed or confirmed cancellation while
        # this read-back was in flight. Its persisted state wins over a stale
        # failure/partial observation; never replay an unconditional update.
        return updated or self.store.operations.get(scope, operation_id)

    def record_result(self, result: DomainResult[Any], *, kind: str, operation_id: str | None = None) -> DomainResult[LongOperationData]:
        """Register the compatibility start/wait path in the same repository."""
        scope = self._scope()
        if scope is None:
            return self._missing_auth()
        now = self.store.clock()
        candidate = OperationRecord(operation_id=operation_id or new_operation_id(), scope_id=scope,
                                    operation_type=kind, state=OperationState.ACCEPTED,
                                    created_at=now, updated_at=now, expires_at=now + RETENTION_SECONDS)
        try:
            current = self.store.operations.get(scope, candidate.operation_id)
            if current is None:
                current, _ = self.store.operations.create(candidate)
            current = self._save_result(scope, current.operation_id, result) or current
            return self._render(current)
        except (StateStoreError, ValueError):
            return self._storage_error()

    async def status(self, operation_id: str) -> DomainResult[LongOperationData]:
        return await self._lookup(operation_id, fetch_result=False)

    async def result(self, operation_id: str) -> DomainResult[LongOperationData]:
        return await self._lookup(operation_id, fetch_result=True)

    async def _lookup(self, operation_id: str, *, fetch_result: bool) -> DomainResult[LongOperationData]:
        scope = self._scope()
        if scope is None:
            return self._missing_auth()
        try:
            record = self.store.operations.get(scope, operation_id)
        except StateStoreError:
            return self._storage_error()
        if record is None:
            return self._not_found()
        if record.expires_at <= self.store.clock():
            return self._expired(record)
        recovery = self._recovery.get(record.operation_type)
        active = self._tasks.get(operation_id)
        if recovery and (record.upstream_chat_id or record.provider_operation_id) and record.state not in _TERMINAL and not (active and not active.done()):
            task = self._refresh_tasks.get(operation_id)
            if task is None or task.done():
                task = asyncio.create_task(self._recover(scope, record, recovery))
                self._refresh_tasks[operation_id] = task
                task.add_done_callback(lambda _task: self._refresh_tasks.pop(operation_id, None))
            result = await asyncio.shield(task)
            if self._scope() != scope:
                return self._not_found()
            return result
        if fetch_result and record.artifacts:
            # Locators are returned without loading/reporting private text.
            # Their prior verification is evidence at completion, not a claim
            # that the file cannot subsequently be removed by its owner.
            if any(item.local_path and not Path(item.local_path).is_file() for item in record.artifacts):
                return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "An operation artifact is no longer available.",
                                            data=self._data(record), verification_status="artifact_missing")
        return self._render(record)

    async def _recover(self, scope: str, record: OperationRecord, recovery: Recovery) -> DomainResult[LongOperationData]:
        lease_id = "lease_" + secrets.token_hex(16)
        claimed = self.store.operations.claim(scope, record.operation_id, lease_id=lease_id,
                                              lease_until=self.store.clock() + LEASE_SECONDS, now=self.store.clock())
        if claimed is None:
            current = self.store.operations.get(scope, record.operation_id)
            return self._render(current) if current is not None else self._not_found()
        heartbeat = asyncio.create_task(self._heartbeat(scope, record.operation_id, lease_id))
        try:
            if self._scope() != scope:
                return self._not_found()
            async with asyncio.timeout(RECOVERY_TIMEOUT_SECONDS):
                result = await recovery(claimed)
            if self._scope() != scope:
                return self._not_found()
            updated = self._save_result(scope, record.operation_id, result, lease_id=lease_id) or record
            return self._render(updated)
        except asyncio.TimeoutError:
            return DomainResult.failure(DomainErrorCode.TIMED_OUT, "Operation recovery did not complete within the read-back deadline.",
                                        data=self._data(record), operation_state=OperationState.TIMED_OUT,
                                        verification_status="completion_not_observed")
        except Exception:
            return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "Operation recovery failed; its source remains available for retry.",
                                        data=self._data(record), verification_status="recovery_failed")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self.store.operations.release(scope, record.operation_id, lease_id=lease_id)

    async def _heartbeat(self, scope: str, operation_id: str, lease_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if not self.store.operations.renew(scope, operation_id, lease_id=lease_id,
                                                lease_until=self.store.clock() + LEASE_SECONDS):
                return

    async def cancel(self, operation_id: str) -> DomainResult[LongOperationData]:
        scope = self._scope()
        if scope is None:
            return self._missing_auth()
        record: OperationRecord | None = None
        try:
            record = self.store.operations.get(scope, operation_id)
            if record is None:
                return self._not_found()
            if record.expires_at <= self.store.clock():
                return self._expired(record)
            if record.state in _TERMINAL or record.state == OperationState.CANCEL_REQUESTED:
                return self._render(record)
            record = self.store.operations.update(scope, operation_id, expected_version=record.version, state=OperationState.CANCEL_REQUESTED,
                                                  verification_status="cancellation_requested") or record
            cancel = self._cancellers.get(record.operation_type)
            if cancel is not None:
                async with asyncio.timeout(10):
                    confirmed = await cancel(record)
                if confirmed is True and self._scope() == scope:
                    current = self.store.operations.get(scope, operation_id)
                    if current is None or current.state in _TERMINAL:
                        return self._render(current) if current is not None else self._not_found()
                    record = self.store.operations.update(scope, operation_id, expected_version=current.version, state=OperationState.CANCELLED,
                                                          verification_status="provider_cancelled") or record
            # Keep a current producer running until it saves its known output;
            # cancel_requested never authorizes deleting the provider source.
            return self._render(record)
        except (StateStoreError, ValueError):
            return self._storage_error()
        except Exception:
            return self._render(record) if record is not None else self._storage_error()

    @staticmethod
    def _data(record: OperationRecord) -> LongOperationData:
        return LongOperationData(operation=record.operation_type, state=record.state, operation_id=record.operation_id,
                                 upstream_operation_id=record.provider_operation_id, upstream_chat_id=record.upstream_chat_id,
                                 latest_upstream_state=record.state.value,
                                 continuation_possible=bool(record.upstream_chat_id or record.provider_operation_id),
                                 report_available=record.operation_type == "research" and bool(record.artifacts),
                                 artifacts=tuple(item.artifact() for item in record.artifacts), created_at=record.created_at,
                                 updated_at=record.updated_at, expires_at=record.expires_at,
                                 cancellation_confirmed=record.verification_status == "provider_cancelled")

    def _render(self, record: OperationRecord) -> DomainResult[LongOperationData]:
        if record.state == OperationState.EXPIRED:
            return self._expired(record)
        data = self._data(record)
        details: dict[str, Any] = {"provider_cancel_verified": data.cancellation_confirmed}
        if record.upstream_chat_id:
            try:
                cleanup = self.store.cleanup_jobs.get(record.scope_id, record.upstream_chat_id)
                if cleanup is not None:
                    details["cleanup"] = {
                        "state": cleanup.state if cleanup.state != "completed" or cleanup.verification_status == "verified_absent" else "failed",
                        "verification_status": cleanup.verification_status,
                        "job_id": cleanup.job_id,
                        "attempts": cleanup.attempts,
                        "diagnostic_id": cleanup.diagnostic_id,
                    }
            except StateStoreError:
                # Available artifacts remain available even if the independent
                # deletion receipt cannot currently be read.
                details["cleanup"] = {"state": "unknown", "verification_status": "local_readback_failed"}
        if record.state in {OperationState.FAILED, OperationState.TIMED_OUT, OperationState.CANCELLED, OperationState.UNAVAILABLE}:
            codes = {OperationState.TIMED_OUT: DomainErrorCode.TIMED_OUT, OperationState.CANCELLED: DomainErrorCode.CANCELLED,
                     OperationState.UNAVAILABLE: DomainErrorCode.CAPABILITY_UNAVAILABLE}
            code = DomainErrorCode(record.error_code) if record.error_code else codes.get(record.state, DomainErrorCode.INTERNAL_ERROR)
            return DomainResult.failure(code, "Operation has not produced a completed result.", data=data,
                                        operation_state=record.state, verification_status=record.verification_status,
                                        retryable=record.state != OperationState.CANCELLED, details=details)
        return DomainResult.success(data, operation_state=record.state, verification_status=record.verification_status,
                                    details=details)

    def _expired(self, record: OperationRecord) -> DomainResult[LongOperationData]:
        return DomainResult.failure(DomainErrorCode.OPERATION_EXPIRED, "Operation metadata has expired.",
                                    data=replace(self._data(record), state=OperationState.EXPIRED),
                                    operation_state=OperationState.EXPIRED, verification_status="metadata_expired")

    @staticmethod
    def _not_found() -> DomainResult[LongOperationData]:
        return DomainResult.failure(DomainErrorCode.OPERATION_NOT_FOUND, "Operation was not found for the current authentication context.")

    @staticmethod
    def _missing_auth() -> DomainResult[LongOperationData]:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "Operation recovery requires an authenticated account context.")

    @staticmethod
    def _storage_error() -> DomainResult[LongOperationData]:
        return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "Local operation metadata could not be accessed.")


_service: OperationService | None = None


def get_operation_service() -> OperationService:
    global _service
    if _service is None:
        _service = OperationService()
    return _service


def persist_recovery_artifacts(
    record: OperationRecord, artifacts: tuple[Artifact, ...], *, operation_state: OperationState | None = None,
    verification_status: str | None = None,
) -> None:
    """Retain read-back artifacts under the claimed operation before deletion."""
    service = get_operation_service()
    if service._scope() != record.scope_id:
        raise StateStoreError("Authentication changed before artifact retention.")
    OperationContext(service, record).observe_artifacts_sync(artifacts, operation_state=operation_state,
                                                            verification_status=verification_status)
