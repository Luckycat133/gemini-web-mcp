"""Explicit long-operation recovery without replaying remote work."""

from __future__ import annotations

import logging
import hashlib
from dataclasses import replace
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from ..domain.artifacts import ArtifactKind, ArtifactState
from ..domain.conversations import CleanupObservation, CleanupState
from ..domain.operations import InlineReportArtifact, OperationReadData, operation_failure_code
from ..domain.results import DomainErrorCode, DomainResult, DomainWarning, OperationState, result_from_exception
from ..infrastructure.account_identity import operation_account_matches
from ..infrastructure.operation_store import RETENTION_SECONDS, OperationRecord, OperationStore
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .artifacts import artifact_from_local_path
from .research_result_storage import (
    RESEARCH_OPERATIONS, RESEARCH_REPORT_PREVIEW_CHARS, ResearchResultUnavailable, load_research_result, save_research_result,
)
from .deadlines import await_before_deadline

logger = logging.getLogger(__name__)


def _account_mismatch() -> DomainResult[Any]:
    return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                verification_status="account_mismatch_or_unbound")


class OperationService:
    """Read one saved research operation using its opaque, explicit handle."""

    def __init__(
        self,
        store: OperationStore,
        client_provider: Callable[[], Any],
        client_initializer: Callable[[], Awaitable[Any]],
        finalize_chat_cleanup: Callable[..., Awaitable[CleanupObservation]] | None = None,
        *, worker_token: str | None = None,
    ):
        self.store = store
        self.client_provider = client_provider
        self.client_initializer = client_initializer
        self.finalize_chat_cleanup = finalize_chat_cleanup
        self.worker_token = worker_token

    def _research_access(self, record: OperationRecord) -> bool:
        return operation_account_matches(record) and (
            self.worker_token is None or self.store.is_worker_owner(record.operation_id, self.worker_token))

    async def save_report(
        self, record: OperationRecord, document: Any, *, include_report: bool = True,
        read_limit: int = 0,
    ) -> DomainResult[OperationReadData]:
        """Save a ready full document before allowing its owned chat to be removed."""
        if not self._research_access(record):
            return _account_mismatch()
        try:
            save_research_result(self.store, record, document)
            current = self.store.get(record.operation_id)
            if current is None or not self._research_access(current):
                return _account_mismatch()
            saved = load_research_result(self.store, current, include_text=include_report)
            if saved is None:
                raise ResearchResultUnavailable("The report was not saved.")
        except (ResearchResultUnavailable, OSError, UnicodeError, ValueError):
            if not self._research_access(record):
                return _account_mismatch()
            return self._report_partial(record, "research_artifact_not_saved")
        return await self._saved_report(current, saved, include_report=include_report,
                                        read_limit=read_limit, client_ready=True)

    async def get_status(self, operation_id: str) -> DomainResult[OperationReadData]:
        return await self._read(operation_id, include_report=False)

    async def get_result(self, operation_id: str) -> DomainResult[OperationReadData]:
        return await self._read(operation_id, include_report=True)

    async def _read(self, operation_id: str, *, include_report: bool) -> DomainResult[OperationReadData]:
        if not operation_id.strip():
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "operation_id must not be blank.")
        try:
            record = self.store.get(operation_id)
        except Exception as error:
            return result_from_exception(error, logger=logger, operation="operation_read")
        if record is None:
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT, "The operation handle was not found.",
                verification_status="operation_not_found",
            )
        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                        verification_status="account_mismatch_or_unbound")
        expired = self.store.clock() - record.created_at >= RETENTION_SECONDS
        expired_protected = expired and record.cleanup_protected and bool(record.upstream_chat_id)
        if expired and not expired_protected:
            self.store.delete_expired(operation_id)
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.",
                verification_status="operation_expired",
            )
        if record.operation in {"gemini_run_task", "gemini_notebook_chat"}:
            from .task_jobs import read_task_operation
            return await read_task_operation(
                record, store=self.store, client_provider=self.client_provider,
                client_initializer=self.client_initializer, finalize_chat_cleanup=self.finalize_chat_cleanup,
                include_answer=include_report,
            )
        if record.media_type:
            return await self._read_media(record, include_artifacts=include_report)
        if not self._research_access(record):
            return _account_mismatch()
        if record.state == "failed" and record.phase == "plan_rejected" and record.error_code == "UPSTREAM_REJECTED":
            cleanup = (CleanupObservation(state=CleanupState(record.cleanup_state), upstream_chat_id=record.upstream_chat_id)
                       if record.cleanup_state in {state.value for state in CleanupState} else None)
            data = OperationReadData(
                operation_id=record.operation_id, operation=record.operation, state=OperationState.FAILED,
                continuation_possible=False, report_available=False,
                verification_status=record.verification_status, cleanup=cleanup, error_code=record.error_code,
            )
            return DomainResult.failure(
                DomainErrorCode.UPSTREAM_REJECTED, "The original research request was rejected.",
                data=data, operation_state=OperationState.FAILED, verification_status=record.verification_status,
            )
        try:
            saved_report = (load_research_result(self.store, record, include_text=include_report)
                            if record.operation in RESEARCH_OPERATIONS else None)
        except (ResearchResultUnavailable, OSError, UnicodeError):
            if not operation_account_matches(record):
                return _account_mismatch()
            return self._report_partial(record, "research_artifact_unverified")
        if saved_report is not None:
            return await self._saved_report(record, saved_report, include_report=include_report)
        if not record.upstream_chat_id:
            if record.worker_token or record.phase not in {"created", "start_returned"}:
                from .background import worker_active
                active = worker_active(record, now=self.store.clock())
                state = (OperationState(record.state) if active or record.state in {"failed", "unavailable"}
                         else OperationState.PARTIAL)
                data = OperationReadData(
                    operation_id=record.operation_id, operation=record.operation, state=state,
                    continuation_possible=active, report_available=False, worker_active=active,
                    verification_status="worker_dispatched" if active else "dispatch_outcome_unverified",
                    error_code=record.error_code,
                )
                return _operation_read_result(data)
            return DomainResult.failure(
                DomainErrorCode.CAPABILITY_UNAVAILABLE,
                "This operation has no upstream chat ID and cannot be recovered.",
                verification_status="continuation_unavailable",
            )
        if record.cleanup_state in {CleanupState.COMPLETED.value, CleanupState.ALREADY_COMPLETED.value}:
            return self._report_partial(record, "legacy_report_artifact_unavailable")
        result = (await self._read_report(record, include_report=True) if include_report
                  else await self._read_research_status(record))
        if expired_protected:
            from dataclasses import replace
            result = replace(result, warnings=(*result.warnings, DomainWarning(
                code="expired_protected_operation",
                message="The normal retention period has elapsed; this handle remains available while cleanup is protected.",
            )))
        return result

    async def _read_research_status(self, record: OperationRecord) -> DomainResult[OperationReadData]:
        """Use only the evidenced progress projection; never fetch a report body."""
        try:
            await self.client_initializer()
            if not self._research_access(record):
                return _account_mismatch()
            running = await _research_running_observed(self.client_provider(), record.upstream_chat_id or "")
            if not self._research_access(record):
                return _account_mismatch()
            state = OperationState.RUNNING if running else OperationState.PARTIAL
            verification = "upstream_running_observed" if running else "progress_unverified"
            if running:
                self.store.update(record.operation_id, state="running", phase="research_running_observed",
                                  verification_status=verification)
            from .background import worker_active
            data = OperationReadData(record.operation_id, record.operation, state,
                True, False, verification, worker_active=worker_active(record, now=self.store.clock()),
                upstream_chat_id=record.upstream_chat_id, upstream_operation_id=record.upstream_operation_id)
            warnings = () if running else (DomainWarning(code="progress_unverified",
                message="Research progress was not independently observed. Read the same operation result to recover a report; no generation was repeated."),)
            return DomainResult.success(data, operation_state=state, verification_status=verification, warnings=warnings)
        except Exception as error:
            if not self._research_access(record):
                return _account_mismatch()
            return result_from_exception(error, logger=logger, operation="research_status")

    async def _read_media(self, record: OperationRecord, *, include_artifacts: bool) -> DomainResult[OperationReadData]:
        from .background import resume_media_worker, worker_active

        active = worker_active(record, now=self.store.clock())
        saved = self.store.get_media_artifacts(record.operation_id)
        artifacts = []
        invalid = False
        for item in saved:
            path = Path(item["local_path"])
            try:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if not path.is_file() or path.stat().st_size != item["size_bytes"] or digest != item["sha256"]:
                    raise ValueError("saved artifact changed")
                artifact = artifact_from_local_path(
                    ArtifactKind(item["kind"]), str(path), source_chat_id=record.upstream_chat_id,
                    requested_backend=record.requested_model, request_model=record.request_model,
                    effective_backend=record.effective_backend,
                )
                if artifact.verification.status.value != "verified":
                    raise ValueError("saved artifact did not verify")
                artifacts.append(replace(artifact, id=item["artifact_id"]))
            except (OSError, ValueError):
                invalid = True
        if invalid or (record.state == "completed" and not artifacts):
            return DomainResult.failure(
                DomainErrorCode.CAPABILITY_UNAVAILABLE, "A saved media artifact is missing or no longer matches its verification.",
                verification_status="local_artifact_unavailable",
            )
        if include_artifacts and not artifacts and record.upstream_chat_id and not active:
            await resume_media_worker(self.store, record)
            record = self.store.get(record.operation_id) or record
            if not operation_account_matches(record):
                return _account_mismatch()
            active = worker_active(record, now=self.store.clock())
        cleanup_state = (CleanupState.RETAINED if record.retain_chat else
                         CleanupState(record.cleanup_state) if record.cleanup_state in {s.value for s in CleanupState}
                         else CleanupState.PENDING)
        cleanup = CleanupObservation(state=cleanup_state, upstream_chat_id=record.upstream_chat_id)
        warnings: tuple[DomainWarning, ...] = ()
        if (include_artifacts and artifacts and not record.retain_chat
                and cleanup_state not in {CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED}
                and (record.cleanup_protected or record.cleanup_state == "not_scheduled")
                and self.finalize_chat_cleanup is not None):
            # Verified local metadata makes result reads repeatable even after deletion.
            if operation_account_matches(record) and self.store.release_completed_media_cleanup(record.operation_id):
                try:
                    cleanup = await self.finalize_chat_cleanup(
                        record.upstream_chat_id, retain_chat=False, delete_after_seconds=record.delete_after_seconds,
                        source=record.operation,
                    )
                    self.store.update_cleanup(record.operation_id, cleanup.state.value)
                except Exception:
                    warnings = (DomainWarning(code="cleanup_unverified", message="Media cleanup remains pending."),)
        state = OperationState.COMPLETED if artifacts else OperationState(record.state)
        if not artifacts and not active and not record.upstream_chat_id and state not in {OperationState.FAILED, OperationState.UNAVAILABLE}:
            state = OperationState.PARTIAL
        data = OperationReadData(
            operation_id=record.operation_id, operation=record.operation, state=state,
            continuation_possible=bool(artifacts or active or (record.upstream_chat_id and cleanup.state not in {
                CleanupState.COMPLETED, CleanupState.ALREADY_COMPLETED,
            })), report_available=False,
            verification_status="local_artifacts_verified" if artifacts else record.verification_status,
            media_type=record.media_type, artifacts=tuple(artifacts) if include_artifacts else (),
            artifact_state=ArtifactState.LOCAL if artifacts else None, worker_active=active,
            error_code=record.error_code, cleanup=cleanup,
        )
        return _operation_read_result(data, warnings=warnings)

    def _report_partial(self, record: OperationRecord, reason: str) -> DomainResult[OperationReadData]:
        cleanup = (CleanupObservation(state=CleanupState(record.cleanup_state), upstream_chat_id=record.upstream_chat_id)
                   if record.cleanup_state in {state.value for state in CleanupState} else None)
        data = OperationReadData(record.operation_id, record.operation, OperationState.PARTIAL,
            False, False, reason, cleanup=cleanup, upstream_chat_id=record.upstream_chat_id,
            upstream_operation_id=record.upstream_operation_id)
        return DomainResult.success(data, operation_state=OperationState.PARTIAL, verification_status=reason,
            warnings=(DomainWarning(code=reason,
                message="The original saved report is unavailable or changed; no research was regenerated."),))

    async def _saved_report(
        self, record: OperationRecord, saved: tuple[Any, str | None], *, include_report: bool,
        read_limit: int = 0, client_ready: bool = False,
    ) -> DomainResult[OperationReadData]:
        artifact, full_text = saved
        warnings: list[DomainWarning] = []
        cleanup = (CleanupObservation(state=CleanupState(record.cleanup_state), upstream_chat_id=record.upstream_chat_id)
                   if record.cleanup_state in {state.value for state in CleanupState} else None)
        removed = record.cleanup_state in {CleanupState.COMPLETED.value, CleanupState.ALREADY_COMPLETED.value}
        if include_report and not removed:
            if record.retain_chat:
                cleanup = CleanupObservation(state=CleanupState.RETAINED, upstream_chat_id=record.upstream_chat_id)
                self.store.update_cleanup(record.operation_id, cleanup.state.value)
            elif self.finalize_chat_cleanup is None:
                warnings.append(DomainWarning(code="cleanup_unverified", message="Cleanup callback unavailable."))
            else:
                try:
                    if not client_ready:
                        await await_before_deadline(self.client_initializer(), timeout=60)
                    if not self._research_access(record):
                        return _account_mismatch()
                    # Rehash after initialization and immediately before releasing
                    # the remote copy. A saved database row alone is insufficient.
                    try:
                        verified = load_research_result(self.store, record, include_text=False)
                    except (ResearchResultUnavailable, OSError, UnicodeError):
                        if not self._research_access(record):
                            return _account_mismatch()
                        return self._report_partial(record, "research_artifact_unverified")
                    if verified is None:
                        return self._report_partial(record, "research_artifact_unverified")
                    self.store.release_cleanup(record.operation_id)
                    cleanup = await self.finalize_chat_cleanup(record.upstream_chat_id, retain_chat=False,
                        delete_after_seconds=record.delete_after_seconds, source=record.operation)
                    if not self._research_access(record):
                        return _account_mismatch()
                    self.store.update_cleanup(record.operation_id, cleanup.state.value)
                    if cleanup.state in {CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.PENDING}:
                        warnings.append(DomainWarning(
                            code="cleanup_pending" if cleanup.state is CleanupState.PENDING else "cleanup_failed",
                            message="Research chat cleanup has not been verified."))
                except Exception as error:
                    logger.warning("Research cleanup remains unverified error_type=%s", type(error).__name__)
                    warnings.append(DomainWarning(code="cleanup_unverified", message="Research chat cleanup was not verified."))
        if not self._research_access(record):
            return _account_mismatch()
        try:
            current = load_research_result(self.store, record, include_text=include_report)
            if current is None:
                raise ResearchResultUnavailable("The saved report was not available.")
            artifact, full_text = current
        except (ResearchResultUnavailable, OSError, UnicodeError):
            if not self._research_access(record):
                return _account_mismatch()
            return self._report_partial(record, "research_artifact_unverified")
        preview = full_text[:RESEARCH_REPORT_PREVIEW_CHARS] if full_text is not None and include_report else None
        inline = None
        if preview is not None:
            encoded = preview.encode("utf-8")
            inline = InlineReportArtifact(id="artifact_" + hashlib.sha256(
                (artifact.id + preview).encode()).hexdigest()[:24], kind="report", mime_type="text/markdown",
                size_bytes=len(encoded), sha256=hashlib.sha256(encoded).hexdigest())
            if len(full_text or "") > len(preview):
                warnings.append(DomainWarning(code="report_preview_truncated",
                    message=f"The preview is limited to {RESEARCH_REPORT_PREVIEW_CHARS} characters; the full report is in its verified local artifact."))
        data = OperationReadData(record.operation_id, record.operation, OperationState.COMPLETED,
            False, True, "research_artifact_verified", report=preview, artifact=inline,
            artifacts=(artifact,) if include_report else (), artifact_state=ArtifactState.LOCAL, read_window_limit=read_limit,
            cleanup=cleanup, upstream_chat_id=record.upstream_chat_id,
            upstream_operation_id=record.upstream_operation_id)
        return DomainResult.success(data, verification_status=data.verification_status, warnings=tuple(warnings))

    async def _read_report(
        self, record: OperationRecord, *, include_report: bool,
    ) -> DomainResult[OperationReadData]:
        try:
            await self.client_initializer()
            if not self._research_access(record):
                return _account_mismatch()
            # Authentication recovery may replace the shared client instance.
            client = self.client_provider()
            response = await client.fetch_latest_chat_response(
                record.upstream_chat_id, limit=10, match=_has_report,
            )
            if not self._research_access(record):
                return _account_mismatch()
            read_limit = 10
            if not _has_report(response):
                # A later user turn must not hide a report just outside the
                # initial window. Keep this bounded rather than scanning all
                # private history.
                read_limit = 100
                if not self._research_access(record):
                    return _account_mismatch()
                response = await client.fetch_latest_chat_response(
                    record.upstream_chat_id, limit=read_limit, match=_has_report,
                )
                if not self._research_access(record):
                    return _account_mismatch()
            document = getattr(response, "deep_research_document", None)
            available = _has_report(response)
            running = False
            if not available and record.state != OperationState.COMPLETED.value and record.upstream_chat_id:
                if not self._research_access(record):
                    return _account_mismatch()
                running = await _research_running_observed(client, record.upstream_chat_id)
                if not self._research_access(record):
                    return _account_mismatch()
            if available:
                if not include_report:
                    data = OperationReadData(record.operation_id, record.operation, OperationState.COMPLETED,
                        True, True, "research_document_readback", read_window_limit=read_limit,
                        upstream_chat_id=record.upstream_chat_id, upstream_operation_id=record.upstream_operation_id)
                    return DomainResult.success(data, verification_status=data.verification_status)
                return await self.save_report(record, document, include_report=include_report, read_limit=read_limit)
            state = OperationState.RUNNING if running else OperationState.PARTIAL
            verification = (
                "report_observed" if available
                else "upstream_running_observed" if running
                else "readback_unverified" if response is None
                else "report_not_observed"
            )
            warnings: tuple[DomainWarning, ...] = ()
            if not available and not running:
                warnings = (DomainWarning(
                    code="readback_unverified" if response is None else "report_not_in_read_window",
                    message=(
                        "The upstream client returned no verifiable response; this can mean empty history or a read failure."
                        if response is None else
                        "No ready report was found in the bounded recent-history window."
                    ),
                    suggested_action="Retry the same operation handle; do not start duplicate research.",
                ),)
            if running:
                self.store.update(
                    record.operation_id, state=state.value,
                    phase="report_observed" if available else "research_running_observed",
                    verification_status=verification,
                )
            if not self._research_access(record):
                return _account_mismatch()
            data = OperationReadData(
                operation_id=record.operation_id, operation=record.operation, state=state,
                continuation_possible=True, report_available=available,
                verification_status=verification,
                read_window_limit=read_limit,
                worker_active=bool(record.worker_token and record.heartbeat_at is not None
                                   and self.store.clock() - record.heartbeat_at < 60),
            )
            return DomainResult.success(data, operation_state=state, verification_status=verification, warnings=warnings)
        except Exception as error:
            if not self._research_access(record):
                return _account_mismatch()
            return result_from_exception(error, logger=logger, operation="operation_read")
def _has_report(response: Any) -> bool:
    document = getattr(response, "deep_research_document", None)
    content = getattr(document, "content", "")
    return bool(getattr(document, "ready", False)) and isinstance(content, str) and bool(content.strip())


def _operation_read_result(data: OperationReadData, *, warnings: tuple[DomainWarning, ...] = ()) -> DomainResult[OperationReadData]:
    if data.state in {OperationState.FAILED, OperationState.UNAVAILABLE, OperationState.TIMED_OUT, OperationState.CANCELLED}:
        return DomainResult.failure(
            operation_failure_code(data.error_code), "The background operation failed; no verified artifact was produced.",
            data=data, operation_state=data.state, verification_status=data.verification_status, warnings=warnings,
        )
    return DomainResult.success(data, operation_state=data.state, verification_status=data.verification_status, warnings=warnings)


async def _research_running_observed(client: Any, chat_id: str) -> bool:
    if not callable(getattr(client, "_batch_execute", None)):
        return False
    try:
        contract = get_contract("research.chat_progress")
        response = await execute_contract(client, contract.key, chat_id=chat_id)
        envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
        if envelope.reject_code is not None:
            return False
        for body in envelope.bodies:
            result = parse_contract_body(contract, body)
            if result.status == "success" and result.value is True:
                return True
    except Exception:
        # Failure to inspect progress must not hide a report or imply running.
        return False
    return False
