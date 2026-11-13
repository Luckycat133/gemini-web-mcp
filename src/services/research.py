"""Shared Deep Research application service for assistance surfaces.

The service owns the asynchronous Deep Research start workflow — fresh research
chat, transport model resolution, plan creation with capability-probe recovery,
and start-with-recovery — below the MCP presentation layers. ``start`` never
waits for the final report: it returns a typed
:class:`~src.domain.LongOperationData` handle immediately, and recoverability
rides on the preserved upstream identifiers instead of any connection-local
MCP state. The plan/start orchestration is shared with the compatibility
``gemini_deep_research`` tool through
:func:`run_deep_research_start_phase`, so both surfaces keep identical phase
deadlines, TIMED_OUT classification, and chat-cleanup scheduling.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

from ..constants import resolve_model_name
from ..domain import (
    CleanupObservation,
    CleanupState,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    LongOperationData,
    OperationState,
    new_operation_id,
    result_from_exception,
)
from ..infrastructure.account_identity import operation_account_matches
from ..infrastructure.operation_store import RETENTION_SECONDS, OperationStore
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope
from .client_timeouts import client_timeout_scope
from .cleanup_scope import observe_cleanup_frames
from .deadlines import await_before_deadline, consume_finished_task as consume_finished_task
from .model_policy import model_policy_scope

logger = logging.getLogger(__name__)


class _SafeResearchLogger(logging.Logger):
    """Keep the shared error classifier from logging upstream response previews."""

    def error(self, msg: object, *args: Any, **kwargs: Any) -> None:
        if len(args) >= 4:
            logger.error("Research failed request_id=%s diagnostic_id=%s error_type=%s",
                         args[1], args[2], args[3])
        else:
            logger.error("Research failed")


_safe_failure_logger = _SafeResearchLogger(__name__ + ".safe")

RESEARCH_CLEANUP_SOURCE = "gemini_research"
DEFAULT_RESEARCH_TIMEOUT_SECONDS = 600
RESEARCH_START_TIMEOUT_SECONDS = 120
RESEARCH_DEFERRED_CLEANUP_SECONDS = 1800


class ResearchCleanupPreconditionError(RuntimeError):
    """A research confirmation cannot proceed without durable cleanup evidence."""


@dataclass(frozen=True)
class _ResearchPlanRejection:
    metadata: tuple[str, str, str]


def research_cleanup_checkpoint(
    store: OperationStore, operation_id: str, plan: Any, chat: Any, *,
    response: Any = None, retain_chat: bool, delete_after_seconds: int | None,
    source: str, schedule_chat_cleanup: Callable[..., Any],
) -> str:
    """Persist guard and cleanup queue before sending a research confirmation."""
    chat_id = research_chat_id(plan=plan, chat=chat, response=response)
    if chat_id is None:
        raise ResearchCleanupPreconditionError("No chat ID was observed before confirmation.")
    existing = store.get(operation_id)
    if not store.attach_late_chat(operation_id, chat_id):
        raise ResearchCleanupPreconditionError("Research cleanup protection is unavailable")
    if existing is not None and (existing.phase not in {"created", "plan_created", "chat_observed", "plan_outcome_unverified"}
                                 or (existing.phase == "plan_outcome_unverified" and not plan)):
        checkpoint_state, checkpoint_phase, checkpoint_verification = (
            existing.state, existing.phase, existing.verification_status,
        )
    else:
        checkpoint_state = OperationState.ACCEPTED.value
        checkpoint_phase = "plan_created" if plan else "chat_observed"
        checkpoint_verification = "plan_observed_start_not_confirmed"
    store.update(
        operation_id, state=checkpoint_state, phase=checkpoint_phase,
        upstream_chat_id=chat_id,
        upstream_operation_id=nonempty_identifier(getattr(plan, "research_id", None)),
        verification_status=checkpoint_verification,
    )
    metadata = getattr(response, "metadata", None) or getattr(chat, "metadata", None)
    if (existing is not None and existing.account_key and isinstance(metadata, (list, tuple))
            and len(metadata) >= 3 and metadata[0] == chat_id
            and all(isinstance(value, str) and value.strip() for value in metadata[:3])):
        store.bind_research_turn(operation_id, account_key=existing.account_key, chat_id=chat_id,
                                 response_id=metadata[1], candidate_id=metadata[2])
    cleanup = schedule_chat_cleanup(
        chat_id, retain_chat=retain_chat,
        delete_after_seconds=(delete_after_seconds if retain_chat else
            max(RESEARCH_DEFERRED_CLEANUP_SECONDS, delete_after_seconds or 0)),
        source=source,
    )
    expected = CleanupState.RETAINED if retain_chat else CleanupState.PENDING
    if not isinstance(cleanup, CleanupObservation) or cleanup.state is not expected:
        raise ResearchCleanupPreconditionError("Research cleanup policy could not be persisted.")
    return chat_id


def research_late_cleanup_checkpoint(
    store: OperationStore, operation_id: str, plan: Any, chat: Any, *, response: Any = None,
    retain_chat: bool, delete_after_seconds: int | None, source: str,
    schedule_chat_cleanup: Callable[..., Any],
) -> None:
    chat_id = research_chat_id(plan=plan, chat=chat, response=response)
    if not chat_id or not store.attach_late_chat(operation_id, chat_id):
        return
    existing = store.get(operation_id)
    metadata = getattr(response, "metadata", None) or getattr(chat, "metadata", None)
    if (existing is not None and existing.account_key and isinstance(metadata, (list, tuple))
            and len(metadata) >= 3 and metadata[0] == chat_id
            and all(isinstance(value, str) and value.strip() for value in metadata[:3])):
        store.bind_research_turn(operation_id, account_key=existing.account_key, chat_id=chat_id,
                                 response_id=metadata[1], candidate_id=metadata[2])
    cleanup = schedule_chat_cleanup(
        chat_id, retain_chat=retain_chat,
        delete_after_seconds=(delete_after_seconds if retain_chat else
                              max(RESEARCH_DEFERRED_CLEANUP_SECONDS, delete_after_seconds or 0)),
        source=source,
    )
    expected = CleanupState.RETAINED if retain_chat else CleanupState.PENDING
    if not isinstance(cleanup, CleanupObservation) or cleanup.state is not expected:
        raise ResearchCleanupPreconditionError("Late research cleanup policy could not be persisted.")


@dataclass(frozen=True)
class ResearchCleanupCheckpoints:
    checkpoint: Callable[..., str]
    late_checkpoint: Callable[..., None]
    early_checkpoint: Callable[[Any], None]


def make_research_cleanup_checkpoints(
    store: OperationStore, record: Any, *, retain_chat: bool, delete_after_seconds: int | None,
    source: str, schedule_chat_cleanup: Callable[..., Any], worker_token: str | None = None,
) -> ResearchCleanupCheckpoints:
    """Bind early and late identities to the operation's account and cleanup guard."""
    operation_id = record.operation_id

    def attach_observed(plan: Any, chat: Any, response: Any = None) -> None:
        cid = research_chat_id(plan=plan, chat=chat, response=response)
        if not cid:
            raise ResearchCleanupPreconditionError("No research chat ID was observed")
        metadata = getattr(response, "metadata", None) or getattr(chat, "metadata", None)
        values = metadata if isinstance(metadata, (list, tuple)) and metadata and metadata[0] == cid else ()
        rid = nonempty_identifier(values[1]) if len(values) > 1 else None
        rcid = nonempty_identifier(values[2]) if len(values) > 2 else None
        if not record.account_key or not store.attach_research_observation(
            operation_id, cid, account_key=record.account_key, response_id=rid, candidate_id=rcid,
        ):
            raise ResearchCleanupPreconditionError("Research identity could not be durably bound")

    def checkpoint(plan: Any, chat: Any, response: Any = None) -> str:
        attach_observed(plan, chat, response)
        if (not operation_account_matches(record)
                or (worker_token is not None and not store.is_worker_owner(operation_id, worker_token))):
            raise ResearchCleanupPreconditionError("Operation account changed")
        return research_cleanup_checkpoint(
            store, operation_id, plan, chat, response=response, retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds, source=source, schedule_chat_cleanup=schedule_chat_cleanup,
        )

    def late_checkpoint(plan: Any, chat: Any, response: Any = None) -> None:
        attach_observed(plan, chat, response)
        if (not operation_account_matches(record)
                or (worker_token is not None and not store.is_worker_owner(operation_id, worker_token))):
            return
        research_late_cleanup_checkpoint(
            store, operation_id, plan, chat, response=response, retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds, source=source, schedule_chat_cleanup=schedule_chat_cleanup,
        )

    def early_checkpoint(observed: Any) -> None:
        checkpoint(None, observed, None)

    return ResearchCleanupCheckpoints(checkpoint, late_checkpoint, early_checkpoint)


@dataclass(frozen=True)
class ResearchServiceDependencies:
    """Client and cleanup seams the research service binds to at call time."""

    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    cleanup_due_remote_chats: Callable[[Any], Awaitable[int]]
    schedule_chat_cleanup: Callable[..., Any]
    resolve_model: Callable[[str], str]
    finalize_chat_cleanup: Callable[..., Awaitable[Any]] | None = None


@dataclass(frozen=True)
class ResearchRequest:
    """One asynchronous Deep Research start request.

    A default cleanup deadline protects an active research chat temporarily.
    """

    query: str
    model: str = "flash"
    thinking_level: str = "standard"
    timeout_seconds: int = DEFAULT_RESEARCH_TIMEOUT_SECONDS
    retain_chat: bool = False
    delete_after_seconds: int | None = None
    cleanup_source: str = RESEARCH_CLEANUP_SOURCE
    operation: str = "gemini_research"
    operation_id: str | None = None


class ResearchService:
    """Start Deep Research asynchronously and return a typed operation handle.

    The service reuses the same native plan/start workflow as the compatibility
    ``gemini_deep_research`` tool (fresh research chat, transport model
    resolution, capability-probe recovery, start-with-recovery) but returns as
    soon as the upstream research has started, issuing one opaque high-entropy
    ``operation_id`` and preserving the upstream operation/chat identifiers in
    structured metadata. It keeps no connection-local operation registry.
    """

    def __init__(self, dependencies: ResearchServiceDependencies):
        self._dependencies = dependencies

    async def start(self, request: ResearchRequest, *, worker_token: str | None = None) -> DomainResult[LongOperationData]:
        if request.operation_id is not None and not re.fullmatch(r"op_[0-9a-f]{32}", request.operation_id):
            return _input_rejected("operation_id must be op_ followed by 32 lowercase hexadecimal characters.")
        query = request.query.strip()
        if not query:
            return _input_rejected("query must not be blank.")
        operation_id = request.operation_id or new_operation_id()
        store = OperationStore()
        try:
            if worker_token is not None:
                record = store.get(operation_id)
                if (record is None or record.operation != request.operation
                        or not store.is_worker_owner(operation_id, worker_token)):
                    return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Worker ownership was not verified.")
                created = record.phase == "created"
            else:
                record, created = store.create_or_get(
                    operation_id, request.operation, retain_chat=request.retain_chat,
                    delete_after_seconds=request.delete_after_seconds,
                    requested_model=request.model, thinking_level=request.thinking_level,
                )
        except Exception:
            logger.exception("Unable to persist research operation metadata")
            return DomainResult.failure(
                DomainErrorCode.INTERNAL_ERROR,
                "Unable to persist research operation metadata; research was not started.",
                verification_status="operation_not_persisted",
            )
        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                        verification_status="account_mismatch_or_unbound")
        if not created:
            recoverable_plan = bool(
                record.phase == "plan_outcome_unverified" and record.upstream_chat_id and record.cleanup_protected
                and record.requested_model and record.thinking_level
            )
            if store.clock() - record.created_at >= RETENTION_SECONDS and not recoverable_plan:
                return DomainResult.failure(
                    DomainErrorCode.INVALID_ARGUMENT, "The operation handle has expired.",
                    verification_status="operation_expired",
                )
            if record.operation != request.operation:
                return _input_rejected("operation_id is already used by a different operation.")
            if recoverable_plan:
                return await self._resume_existing_plan(store, record, request.timeout_seconds)
            data = LongOperationData(
                operation=record.operation, state=OperationState(record.state),
                operation_id=record.operation_id, upstream_chat_id=record.upstream_chat_id,
                upstream_operation_id=record.upstream_operation_id,
                continuation_possible=bool(record.upstream_chat_id),
                report_available=False,
            )
            existing = research_domain_result(data)
            return replace(
                existing,
                meta=replace(existing.meta, verification_status=record.verification_status),
                warnings=(*existing.warnings, DomainWarning(
                    code="operation_already_exists",
                    message="The handle was already claimed; no new research was started.",
                )),
            )

        try:
            client = await self._prepare_client(record)
        except Exception as error:
            result = _failure_from_exception(error, request, operation_id=operation_id, failure_phase="initialization")
            store.update(operation_id, state=result.meta.operation_state.value, phase="initialization_failed",
                         verification_status=result.meta.verification_status)
            if operation_account_matches(record):
                store.release_cleanup(operation_id)
            return result

        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                        verification_status="account_mismatch_or_unbound")
        if not has_native_research_api(client):
            store.update(operation_id, state=OperationState.UNAVAILABLE.value, phase="capability_unavailable",
                         verification_status="capability_not_available")
            store.release_cleanup(operation_id)
            return _capability_unavailable(request)

        research_model, model_note = resolve_deep_research_transport_model(request.model, client)

        checkpoints = make_research_cleanup_checkpoints(
            store, record, retain_chat=request.retain_chat, delete_after_seconds=request.delete_after_seconds,
            source=request.cleanup_source, schedule_chat_cleanup=self._dependencies.schedule_chat_cleanup,
            worker_token=worker_token,
        )

        start = await run_deep_research_start_phase(
            client,
            query=query,
            requested_model=request.model,
            resolved_model=self._dependencies.resolve_model(request.model),
            research_model=research_model,
            model_note=model_note,
            thinking_level=request.thinking_level,
            timeout_seconds=request.timeout_seconds,
            operation=request.operation,
            operation_id=operation_id,
            schedule_chat_cleanup=self._dependencies.schedule_chat_cleanup,
            retain_chat=request.retain_chat,
            delete_after_seconds=request.delete_after_seconds,
            cleanup_source=request.cleanup_source,
            checkpoint=checkpoints.checkpoint,
            late_checkpoint=checkpoints.late_checkpoint,
            early_checkpoint=checkpoints.early_checkpoint,
            claim_confirmation=lambda: operation_account_matches(record)
                and (worker_token is None or store.is_worker_owner(operation_id, worker_token))
                and store.claim_research_confirmation(operation_id, expected_phase="plan_created"),
        )
        if (not operation_account_matches(record)
                or (worker_token is not None and not store.is_worker_owner(operation_id, worker_token))):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                        verification_status="account_mismatch_or_unbound")
        if start.timed_out is not None:
            result = start.timed_out
        elif start.error is not None:
            if research_start_outcome_unknown(start):
                result = research_unknown_result(request.operation, operation_id, start.plan, start.chat)
            else:
                result = _failure_from_exception(
                    start.error, request, operation_id=operation_id, plan=start.plan, chat=start.chat,
                    failure_phase=start.failure_phase,
                )
        else:
            state = (OperationState.COMPLETED if _has_ready_research_document(start.start_output)
                     else operation_state_from_upstream(getattr(start.start_output, "state", None)))
            if state is OperationState.COMPLETED:
                document = getattr(start.start_output, "deep_research_document", None)
                content = getattr(document, "content", "")
                if not (getattr(document, "ready", False) and isinstance(content, str) and content.strip()):
                    state = OperationState.PARTIAL
            data = research_operation_data(
                state, operation=request.operation, operation_id=operation_id, plan=start.plan,
                chat=start.chat, response=start.start_output,
                latest_upstream_state=upstream_state(getattr(start.start_output, "state", None)), poll_count=0,
            )
            result = research_domain_result(data)
        assert result.data is not None
        try:
            store.update(
                operation_id, state=result.data.state.value,
                phase=("plan_outcome_unverified" if research_start_outcome_unknown(start)
                       or (start.timed_out is not None and start.failure_phase == "plan") else "start_returned"),
                upstream_chat_id=result.data.upstream_chat_id,
                upstream_operation_id=result.data.upstream_operation_id,
                verification_status=result.meta.verification_status,
            )
        except Exception:
            logger.exception("Unable to update research operation metadata")
            return DomainResult.failure(
                DomainErrorCode.INTERNAL_ERROR,
                "Research outcome could not be saved; use any observed upstream chat ID to recover it.",
                data=result.data,
                verification_status="operation_not_persisted",
            )
        if start.error is None and start.timed_out is None and _has_ready_research_document(start.start_output):
            from .operations import OperationService
            current = store.get(operation_id)
            if current is None:
                return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR,
                    "The original research operation could not be read back.")
            saved = await OperationService(
                store, self._dependencies.client_provider, self._dependencies.client_initializer,
                self._dependencies.finalize_chat_cleanup, worker_token=worker_token,
            ).save_report(current, getattr(start.start_output, "deep_research_document", None))
            saved_data = saved.data
            return DomainResult(
                ok=saved.ok,
                data=(replace(result.data, state=saved_data.state, report_available=saved_data.report_available,
                              cleanup=saved_data.cleanup, continuation_possible=not saved_data.report_available)
                      if saved_data is not None else None),
                error=saved.error, warnings=saved.warnings, meta=saved.meta,
            )
        definitely_failed = (
            (start.error is not None and not start.start_attempted and not research_start_outcome_unknown(start))
            or (start.error is None and start.timed_out is None
                and result.data.state in {OperationState.FAILED, OperationState.CANCELLED})
        )
        if definitely_failed and not request.retain_chat and operation_account_matches(record):
            try:
                store.release_cleanup(operation_id)
                if result.data.upstream_chat_id and self._dependencies.finalize_chat_cleanup is not None:
                    cleanup = await self._dependencies.finalize_chat_cleanup(
                        result.data.upstream_chat_id, retain_chat=False,
                        delete_after_seconds=request.delete_after_seconds, source=request.cleanup_source,
                    )
                    store.update_cleanup(operation_id, cleanup.state.value)
                    result = replace(result, data=replace(
                        result.data, cleanup=cleanup,
                        continuation_possible=cleanup.state.value not in {"completed", "already_completed"},
                    ))
            except Exception:
                logger.exception("Failed to finalize research cleanup")
                result = replace(result, warnings=(*result.warnings, DomainWarning(
                    code="cleanup_unverified", message="Research chat cleanup was not verified.",
                )))
        return result

    async def _resume_existing_plan(self, store: OperationStore, record: Any, timeout: int) -> DomainResult[LongOperationData]:
        """Confirm an observed original plan once, without submitting its query again."""
        if not operation_account_matches(record):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Operation account could not be verified.",
                                        verification_status="account_mismatch_or_unbound")
        original = research_unknown_result(record.operation, record.operation_id, None,
                                           SimpleNamespace(cid=record.upstream_chat_id))
        try:
            async def prepare_and_read() -> tuple[Any, Any]:
                client = await self._prepare_client(record)
                if not operation_account_matches(record):
                    return client, None
                return client, await read_existing_research_plan(client, record.upstream_chat_id)

            client, plan = await await_before_deadline(prepare_and_read(), timeout=phase_timeout(timeout))
            if plan is None:
                return original
            if isinstance(plan, _ResearchPlanRejection):
                cid, rid, rcid = plan.metadata
                if (not operation_account_matches(record) or not record.account_key
                        or not store.reject_research_turn(record.operation_id, account_key=record.account_key,
                                                          chat_id=cid, response_id=rid, candidate_id=rcid)):
                    return original
                cleanup = None
                if self._dependencies.finalize_chat_cleanup is not None and operation_account_matches(record):
                    try:
                        cleanup = await self._dependencies.finalize_chat_cleanup(
                            cid, retain_chat=record.retain_chat, delete_after_seconds=record.delete_after_seconds,
                            source=record.operation,
                        )
                        if operation_account_matches(record):
                            store.update_cleanup(record.operation_id, cleanup.state.value)
                    except Exception:
                        pass
                data = LongOperationData(operation=record.operation, state=OperationState.FAILED,
                                         operation_id=record.operation_id, upstream_chat_id=cid,
                                         cleanup=cleanup, error_code="UPSTREAM_REJECTED")
                return DomainResult.failure(DomainErrorCode.UPSTREAM_REJECTED, "The research plan request was rejected.",
                                            data=data, operation_state=OperationState.FAILED,
                                            verification_status="research_rejection_observed")
            metadata = getattr(plan, "metadata", None)
            if (not isinstance(metadata, (list, tuple)) or len(metadata) < 3
                    or metadata[0] != record.upstream_chat_id
                    or getattr(plan, "cid", None) != record.upstream_chat_id
                    or (record.upstream_response_id and metadata[1] != record.upstream_response_id)
                    or (record.upstream_candidate_id and metadata[2] != record.upstream_candidate_id)):
                return original
            model, _note = resolve_deep_research_transport_model(record.requested_model, client)
            # A fallback unspecified model cannot prove preservation of the
            # original selection, so do not confirm on that basis.
            if is_default_deep_research_transport(model):
                return original
            registry = getattr(client, "_model_registry", None)
            if not isinstance(registry, dict):
                return original
            if isinstance(model, str):
                exact = [item for item in registry.values()
                         if str(getattr(item, "model_name", "")).lower() == model.lower()]
                if len(exact) != 1:
                    return original
                model = exact[0]
            if not any(model is item for item in registry.values()):
                return original
            chat = client.start_chat(metadata=list(plan.metadata), cid=plan.cid, model=model)
        except Exception:
            return original

        # A crash after this commit is intentionally not retried: it may have
        # sent the confirmation even when its response was lost.
        if not operation_account_matches(record):
            return original
        if not store.claim_research_confirmation(record.operation_id, expected_phase="plan_outcome_unverified"):
            return original
        try:
            with research_thinking_scope(client, model, self._dependencies.resolve_model(record.requested_model),
                                         record.thinking_level), model_policy_scope("research"):
                output = await start_deep_research_with_recovery(
                    client, plan, chat, timeout=min(phase_timeout(timeout), RESEARCH_START_TIMEOUT_SECONDS),
                )
            if not operation_account_matches(record):
                return original
            state = (OperationState.COMPLETED if _has_ready_research_document(output)
                     else operation_state_from_upstream(getattr(output, "state", None)))
            if getattr(output, "timeout_during_start", False):
                state = OperationState.PARTIAL
            data = research_operation_data(state, operation=record.operation, operation_id=record.operation_id,
                                           plan=plan, chat=chat, response=output)
            store.update(record.operation_id, state=data.state.value, phase="confirmation_returned",
                         upstream_chat_id=data.upstream_chat_id, upstream_operation_id=data.upstream_operation_id,
                         verification_status="report_observed" if data.report_available else "start_outcome_unverified")
            result = research_domain_result(data)
            return replace(result, warnings=(*result.warnings, DomainWarning(
                code="operation_already_exists", message="The saved plan was confirmed; the new query was ignored.",
            )))
        except Exception:
            return original

    async def _prepare_client(self, record: Any) -> Any:
        client = self._dependencies.client_provider()
        await self._dependencies.client_initializer()
        if not operation_account_matches(record):
            raise ResearchCleanupPreconditionError("Operation account changed during initialization")
        await self._dependencies.cleanup_due_remote_chats(client)
        if not operation_account_matches(record):
            raise ResearchCleanupPreconditionError("Operation account changed during initialization")
        return client


@dataclass(frozen=True)
class DeepResearchStart:
    """Outcome of one shared Deep Research plan/start phase run.

    ``chat``, ``plan`` and ``start_output`` preserve the partial upstream
    state so each surface can report the upstream identifiers. ``timed_out``
    or ``error`` is set only when a phase failed; chat cleanup has already
    been scheduled by :func:`run_deep_research_start_phase` in that case (and
    on success).
    """

    chat: Any
    plan: Any | None = None
    start_output: Any | None = None
    timed_out: DomainResult[LongOperationData] | None = None
    error: BaseException | None = None
    start_attempted: bool = False
    failure_phase: str | None = None


async def run_deep_research_start_phase(
    client: Any,
    *,
    query: str,
    requested_model: str,
    resolved_model: str,
    research_model: Any,
    model_note: str,
    thinking_level: str,
    timeout_seconds: int,
    operation: str,
    operation_id: str | None,
    schedule_chat_cleanup: Callable[..., Any],
    retain_chat: bool,
    delete_after_seconds: int | None,
    cleanup_source: str,
    checkpoint: Callable[[Any, Any, Any], str] | None = None,
    late_checkpoint: Callable[[Any, Any, Any], None] | None = None,
    early_checkpoint: Callable[[Any], None] | None = None,
    claim_confirmation: Callable[[], bool] | None = None,
) -> DeepResearchStart:
    """Run the shared Deep Research plan/start phases for both surfaces.

    The asynchronous ``gemini_research`` service and the compatibility
    ``gemini_deep_research`` tool start Deep Research identically: fresh
    research chat, optional thinking scope, plan under the request deadline
    (with the ``phase_timeout`` floor), and start-with-recovery capped at
    ``RESEARCH_START_TIMEOUT_SECONDS``. This coroutine owns that
    orchestration: it converts a missed phase deadline into one typed
    TIMED_OUT result, captures any other exception for the caller's
    classifier, and schedules retention-aware chat cleanup exactly once in
    ``finally``.
    """
    chat = None
    plan = None
    start_output = None
    start_attempted = False
    checkpointed_chat_id: str | None = None
    observed_chat: Any = None
    active = True
    phase = "chat_setup"

    def observe_identity(value: Any) -> None:
        nonlocal observed_chat, checkpointed_chat_id
        if phase != "plan" or early_checkpoint is None:
            return
        cid = research_chat_id(chat=value, response=value)
        if not cid:
            return
        if observed_chat is not None and observed_chat.cid != cid:
            raise ResearchCleanupPreconditionError("Research chat identity changed")
        metadata = getattr(value, "metadata", None)
        metadata = list(metadata) if isinstance(metadata, (list, tuple)) else [cid]
        if not metadata:
            metadata = [cid]
        metadata[0] = cid
        observed_chat = SimpleNamespace(cid=cid, metadata=metadata)
        if not active and late_checkpoint is not None:
            late_checkpoint(None, observed_chat, None)
        elif active and early_checkpoint is not None:
            early_checkpoint(observed_chat)
            checkpointed_chat_id = cid

    def safe_chat() -> Any:
        return observed_chat if observed_chat is not None and research_chat_id(chat=chat) != observed_chat.cid else chat

    def checkpoint_late(done: asyncio.Future[Any]) -> None:
        try:
            output = done.result()
        except BaseException:
            output = None
        late_id = research_chat_id(plan=plan, chat=chat, response=output)
        if not late_id:
            return
        if late_id == checkpointed_chat_id:
            # The pre-confirmation checkpoint already durably registered this
            # exact chat. A late cancellation must not enqueue it again.
            return
        if late_checkpoint is not None:
            late_checkpoint(plan, chat, output)
        else:
            schedule_chat_cleanup(
                late_id, retain_chat=retain_chat,
                delete_after_seconds=(delete_after_seconds if retain_chat else
                                      max(RESEARCH_DEFERRED_CLEANUP_SECONDS, delete_after_seconds or 0)),
                source=cleanup_source,
            )

    try:
        chat = start_fresh_research_chat(client, research_model)
        scope = research_thinking_scope(client, research_model, resolved_model, thinking_level)
        with client_timeout_scope(client, phase_timeout(timeout_seconds)), scope, observe_cleanup_frames(observe_identity), model_policy_scope("research"):
            phase = "plan"
            plan = await await_before_deadline(
                create_deep_research_plan(
                    client,
                    format_research_query(query, requested_model, model_note),
                    chat=chat,
                    model=research_model,
                ),
                timeout=phase_timeout(timeout_seconds),
                on_late=checkpoint_late,
            )
            if observed_chat is not None and research_chat_id(plan=plan, chat=chat) != observed_chat.cid:
                raise ResearchCleanupPreconditionError("Research chat identity changed")
            phase = "checkpoint"
            if checkpoint is not None:
                checkpointed_chat_id = checkpoint(plan, chat, None)
            start_attempted = True
            if claim_confirmation is not None and not claim_confirmation():
                raise RuntimeError("Research confirmation was already claimed or is unavailable")
            phase = "start"
            start_output = await start_deep_research_with_recovery(
                client,
                plan,
                chat,
                timeout=min(phase_timeout(timeout_seconds), RESEARCH_START_TIMEOUT_SECONDS),
                on_late=checkpoint_late,
            )
            if getattr(start_output, "timeout_during_start", False):
                return DeepResearchStart(
                    chat,
                    plan,
                    start_output,
                    timed_out=research_timed_out_result(
                        operation=operation,
                        operation_id=operation_id,
                        plan=plan,
                        chat=chat,
                        start_output=start_output,
                    ),
                    start_attempted=start_attempted,
                    failure_phase=phase,
                )
    except asyncio.TimeoutError:
        return DeepResearchStart(
            safe_chat(),
            plan,
            start_output,
            timed_out=research_timed_out_result(
                operation=operation,
                operation_id=operation_id,
                plan=plan,
                chat=safe_chat(),
                start_output=start_output,
            ),
            start_attempted=start_attempted,
            failure_phase=phase,
        )
    except Exception as error:
        # The SDK may receive a completed research document on the plan turn,
        # then raise because no plan was present. Preserve that exact output;
        # do not synthesize a plan or submit a confirmation.
        output = getattr(chat, "last_output", None) if chat is not None else None
        if (phase == "plan" and _safe_research_failure_reason(error) in {"plan_missing", "research_no_output", "no_output"}
                and _has_ready_research_document(output)):
            try:
                if checkpoint is not None:
                    checkpointed_chat_id = checkpoint(None, chat, output)
                elif not research_chat_id(plan=None, chat=chat, response=output):
                    raise RuntimeError("Ready research document has no recoverable chat ID")
                return DeepResearchStart(chat, None, output, start_attempted=False)
            except Exception as checkpoint_error:
                return DeepResearchStart(chat, None, output, error=checkpoint_error, start_attempted=True,
                                         failure_phase="checkpoint")
        return DeepResearchStart(safe_chat(), plan, start_output, error=error, start_attempted=start_attempted,
                                 failure_phase=phase)
    finally:
        active = False
        # Backstop any ID first seen after the pre-confirmation checkpoint.
        chat_id = research_chat_id(plan=plan, chat=safe_chat(), response=start_output)
        if chat_id is not None and chat_id != checkpointed_chat_id:
            try:
                if checkpoint is not None:
                    # Guard the newly observed CID before making its cleanup
                    # visible to any process. Never enqueue an unguarded chat.
                    checkpoint(plan, safe_chat(), start_output)
                else:
                    schedule_chat_cleanup(
                        chat_id,
                        retain_chat=retain_chat,
                        delete_after_seconds=(
                            delete_after_seconds if retain_chat else
                            max(RESEARCH_DEFERRED_CLEANUP_SECONDS, delete_after_seconds or 0)
                        ),
                        source=cleanup_source,
                    )
            except Exception:
                logger.exception("Research cleanup backstop could not be registered")
    return DeepResearchStart(chat, plan, start_output, start_attempted=start_attempted)


def _has_ready_research_document(response: Any) -> bool:
    document = getattr(response, "deep_research_document", None)
    content = getattr(document, "content", None)
    return bool(getattr(document, "ready", False)) and isinstance(content, str) and bool(content.strip())


def research_thinking_scope(
    client: Any,
    research_model: Any,
    resolved_model: str,
    thinking_level: str,
) -> Any:
    """Resolve the thinking scope for one Deep Research start request."""
    if is_default_deep_research_transport(research_model):
        return null_scope()
    thinking_scope = getattr(client, "thinking_scope", None)
    if thinking_scope is None:
        return null_scope()
    return thinking_scope(resolved_model, thinking_level)


def has_native_research_api(client: Any) -> bool:
    """Report whether the client exposes the native plan/start/wait API.

    ``wait_for_deep_research`` is required even though the asynchronous
    ``gemini_research`` service never waits for the final report: this probe
    is shared with the compatibility ``gemini_deep_research`` tool, whose
    wait-for-completion mode polls that same API, so both surfaces gate on
    one identical capability contract.
    """
    return all(
        hasattr(client, attr)
        for attr in (
            "create_deep_research_plan",
            "start_deep_research",
            "wait_for_deep_research",
        )
    )


def nonempty_identifier(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def research_chat_id(
    *,
    plan: Any = None,
    chat: Any = None,
    response: Any = None,
) -> str | None:
    for owner in (chat, plan, response):
        identifier = nonempty_identifier(getattr(owner, "cid", None))
        if identifier:
            return identifier
    metadata = getattr(response, "metadata", None)
    if isinstance(metadata, list) and metadata:
        return nonempty_identifier(metadata[0])
    return None


def upstream_state(value: Any) -> str | None:
    if value is None:
        return None
    state = getattr(value, "state", value)
    if isinstance(state, OperationState):
        return state.value
    if isinstance(state, str) and state.strip():
        return state.strip().lower()
    return None


def operation_state_from_upstream(value: Any) -> OperationState:
    state = upstream_state(value)
    if state in {"accepted", "pending", "queued", "scheduled"}:
        return OperationState.QUEUED
    if state in {"complete", "completed", "done", "success", "succeeded"}:
        return OperationState.COMPLETED
    if state in {"timed_out", "timeout"}:
        return OperationState.TIMED_OUT
    if state in {"cancelled", "canceled"}:
        return OperationState.CANCELLED
    if state in {"failed", "error"}:
        return OperationState.FAILED
    if state in {"running", "processing", "in_progress"}:
        return OperationState.RUNNING
    return OperationState.ACCEPTED


def research_operation_data(
    state: OperationState,
    *,
    operation: str = "gemini_deep_research",
    operation_id: str | None = None,
    plan: Any = None,
    chat: Any = None,
    response: Any = None,
    upstream_result: Any = None,
    latest_upstream_state: str | None = None,
    poll_count: int | None = None,
) -> LongOperationData:
    if plan is None:
        plan = getattr(upstream_result, "plan", None)
    if response is None:
        response = getattr(upstream_result, "start_output", None)
    statuses = list(getattr(upstream_result, "statuses", []) or [])
    if latest_upstream_state is None and statuses:
        latest_upstream_state = upstream_state(statuses[-1])
    if poll_count is None:
        poll_count = getattr(upstream_result, "poll_count", None)
    if not isinstance(poll_count, int):
        poll_count = len(statuses)

    upstream_operation_id = nonempty_identifier(getattr(plan, "research_id", None))
    chat_id = research_chat_id(plan=plan, chat=chat, response=response)
    final_output = getattr(upstream_result, "final_output", None)
    if final_output is None and state is OperationState.COMPLETED:
        final_output = response
    document = getattr(final_output, "deep_research_document", None) if final_output else None
    report_text = getattr(document, "content", "") if document else ""
    report_available = (
        state is OperationState.COMPLETED
        and bool(getattr(document, "ready", False))
        and isinstance(report_text, str)
        and bool(report_text.strip())
    )
    return LongOperationData(
        operation=operation,
        state=state,
        operation_id=operation_id,
        upstream_operation_id=upstream_operation_id,
        upstream_chat_id=chat_id,
        title=nonempty_identifier(getattr(plan, "title", None)),
        latest_upstream_state=latest_upstream_state,
        continuation_possible=bool(chat_id),
        report_available=report_available,
        poll_count=max(0, poll_count),
    )


def research_start_outcome_unknown(start: DeepResearchStart) -> bool:
    return bool(
        start.error is not None and start.failure_phase == "plan"
        and research_chat_id(plan=start.plan, chat=start.chat, response=start.start_output)
    )


def research_unknown_result(operation: str, operation_id: str, plan: Any, chat: Any) -> DomainResult[LongOperationData]:
    data = research_operation_data(OperationState.PARTIAL, operation=operation, operation_id=operation_id,
                                   plan=plan, chat=chat)
    return DomainResult.success(
        data, operation_state=OperationState.PARTIAL, verification_status="start_outcome_unverified",
        warnings=(DomainWarning(
            code="research_outcome_unknown",
            message="The research request was sent, but its outcome could not be verified; do not start it again.",
            suggested_action="Inspect the same operation handle later; do not start duplicate research.",
        ),),
    )


def research_domain_result(
    data: LongOperationData,
    *,
    message: str = "",
) -> DomainResult[LongOperationData]:
    details = {
        "service": "research",
        "operation_handle_issued": bool(data.operation_id),
        "upstream_operation_id_observed": bool(data.upstream_operation_id),
        "upstream_chat_id_observed": bool(data.upstream_chat_id),
        "continuation_possible": data.continuation_possible,
        "poll_count": data.poll_count,
    }
    if data.state is OperationState.TIMED_OUT:
        return DomainResult.failure(
            DomainErrorCode.TIMED_OUT,
            message or "Deep Research did not complete before the configured deadline.",
            data=data,
            retryable=True,
            suggested_action=(
                "Use the preserved upstream chat ID to inspect the report later, or retry with a longer timeout."
                if data.continuation_possible
                else "Retry with a longer timeout."
            ),
            operation_state=OperationState.TIMED_OUT,
            verification_status="completion_not_observed",
            details=details,
        )
    if data.state in {
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.UNAVAILABLE,
    }:
        error_code = (
            DomainErrorCode.CANCELLED
            if data.state is OperationState.CANCELLED
            else DomainErrorCode.INTERNAL_ERROR
        )
        return DomainResult.failure(
            error_code,
            message or "Deep Research failed before completion.",
            data=data,
            retryable=data.state is not OperationState.CANCELLED,
            suggested_action="Inspect server diagnostics and retry.",
            operation_state=data.state,
            verification_status="operation_failed",
            details=details,
        )
    verification_status = {
        OperationState.COMPLETED: (
            "report_observed" if data.report_available else "upstream_completed_report_not_observed"
        ),
        OperationState.QUEUED: "upstream_queued",
        OperationState.RUNNING: "upstream_running",
        OperationState.ACCEPTED: "start_outcome_unverified",
        OperationState.PARTIAL: "report_not_observed",
    }.get(data.state, "upstream_state_observed")
    return DomainResult.success(
        data,
        operation_state=data.state,
        verification_status=verification_status,
        details=details,
    )


def research_timed_out_result(
    *,
    operation: str = "gemini_deep_research",
    operation_id: str | None = None,
    plan: Any = None,
    chat: Any = None,
    start_output: Any = None,
) -> DomainResult[LongOperationData]:
    """Build the typed TIMED_OUT result for one missed Deep Research deadline."""
    data = research_operation_data(
        OperationState.TIMED_OUT,
        operation=operation,
        operation_id=operation_id,
        plan=plan,
        chat=chat,
        response=start_output,
        latest_upstream_state=upstream_state(start_output),
    )
    return research_domain_result(data)


async def read_existing_research_plan(client: Any, cid: str):
    """Read a complete original plan with the evidence-backed RPC contract."""
    contract = get_contract("research.chat_plan")
    response = await execute_contract(client, contract.key, chat_id=cid)
    if getattr(response, "status_code", None) != 200:
        return None
    envelope = parse_rpc_envelope(getattr(response, "text", "") or "", contract.rpc_id)
    if envelope.reject_code is not None or not envelope.parsed or not envelope.bodies:
        return None
    parsed = parse_contract_body(contract, envelope.bodies[0], chat_id=cid)
    if parsed.status == "rejected" and isinstance(parsed.value, dict):
        metadata = parsed.value.get("metadata")
        if isinstance(metadata, list) and len(metadata) == 3 and all(isinstance(item, str) and item for item in metadata):
            return _ResearchPlanRejection(tuple(metadata))
    if parsed.status != "success" or not isinstance(parsed.value, dict):
        return None
    from gemini_webapi.types import DeepResearchPlan
    payload = dict(parsed.value["plan"])
    payload["metadata"] = parsed.value["metadata"]
    payload["cid"] = cid
    return DeepResearchPlan(**payload)


async def create_deep_research_plan(client: Any, query: str, chat: Any, model: Any):
    try:
        return await client.create_deep_research_plan(query, chat=chat, model=model)
    except Exception as e:
        cid = research_chat_id(chat=chat)
        if (cid and _safe_research_failure_reason(e) in {"plan_missing", "no_output", "research_no_output"}
                and not _has_ready_research_document(getattr(chat, "last_output", None))):
            try:
                recovered = await read_existing_research_plan(client, cid)
                if recovered is not None and not isinstance(recovered, _ResearchPlanRejection):
                    chat.metadata = list(recovered.metadata)
                    chat.cid, chat.rid, chat.rcid = recovered.metadata[:3]
                    return recovered
            except Exception:
                logger.warning("Research plan readback could not be verified")
        if not is_capability_probe_false_negative(e) or not all(
            hasattr(client, attr)
            for attr in ("_deep_research_preflight", "_collect_research_output")
        ):
            raise

        logger.warning("Deep Research capability probe failed; trying direct research request")
        await client._deep_research_preflight()
        output = await client._collect_research_output(chat, query)
        plan = getattr(output, "deep_research_plan", None)
        if not plan:
            raise
        plan.metadata = list(getattr(chat, "metadata", []) or [])
        plan.cid = getattr(chat, "cid", "") or getattr(plan, "cid", "")
        if not getattr(plan, "confirm_prompt", ""):
            plan.confirm_prompt = "Start research"
        if not getattr(plan, "response_text", ""):
            plan.response_text = getattr(output, "text", "")
        return plan


def start_fresh_research_chat(client: Any, model: Any):
    """Create a chat that is not polluted by gemini_webapi's shared default metadata."""
    chat = client.start_chat(model=model)
    for attr in ("cid", "rid", "rcid"):
        try:
            setattr(chat, attr, "")
        except Exception:
            logger.debug("Could not clear fresh research chat %s", attr)
    return chat


async def start_deep_research_with_recovery(
    client: Any, plan: Any, chat: Any, timeout: int,
    on_late: Callable[[asyncio.Future[Any]], None] | None = None,
):
    try:
        return await await_before_deadline(
            client.start_deep_research(plan, chat=chat),
            timeout=timeout,
            on_late=on_late,
        )
    except asyncio.TimeoutError:
        logger.warning("Deep Research start timed out; continuing with chat-history polling")
        latest = None
        cid = getattr(chat, "cid", None) or getattr(plan, "cid", None)
        if cid and hasattr(client, "fetch_latest_chat_response"):
            latest = await client.fetch_latest_chat_response(cid)
        return latest or SimpleNamespace(text="", timeout_during_start=True)


def is_capability_probe_false_negative(error: Exception) -> bool:
    text = str(error)
    return "appears not eligible for deep research" in text and "Failed: []" in text


def is_default_deep_research_transport(model: Any) -> bool:
    return getattr(model, "model_name", None) == "unspecified" or model == "unspecified"


def resolve_deep_research_transport_model(requested_model: str, client: Any = None) -> tuple[Any, str]:
    """Preserve the requested family; the client rejects unavailable models."""
    if requested_model in {"", None}:
        requested_model = "flash"
    normalized = str(requested_model).strip().lower()
    resolved = resolve_model_name(normalized)

    # The SDK's fuzzy fallback may pick Flash-Lite for Flash. Accept only a
    # descriptor actually present in the runtime registry and in the exact
    # requested family; never infer the selected model from default transport.
    families = {
        "flash": ("gemini-3-flash", {"gemini-3-flash", "gemini-flash"}),
        "fast": ("gemini-3-flash", {"gemini-3-flash", "gemini-flash"}),
        "thinking": ("gemini-3-flash", {"gemini-3-flash", "gemini-flash"}),
        "gemini-3-flash-thinking": ("gemini-3-flash", {"gemini-3-flash", "gemini-flash"}),
        "gemini-flash": ("gemini-3-flash", {"gemini-3-flash", "gemini-flash"}),
        "gemini-3-flash": ("gemini-3-flash", {"gemini-3-flash", "gemini-flash"}),
        "pro": ("gemini-3-pro", {"gemini-3-pro", "gemini-pro"}),
        "gemini-pro": ("gemini-3-pro", {"gemini-3-pro", "gemini-pro"}),
        "gemini-3-pro": ("gemini-3-pro", {"gemini-3-pro", "gemini-pro"}),
        "argon": ("gemini-argon", {"gemini-argon", "gemini-4-argon"}),
        "4 argon": ("gemini-argon", {"gemini-argon", "gemini-4-argon"}),
        "gemini-argon": ("gemini-argon", {"gemini-argon", "gemini-4-argon"}),
        "gemini-4-argon": ("gemini-argon", {"gemini-argon", "gemini-4-argon"}),
        "flash-lite": ("flash-lite", {"gemini-flash-lite", "gemini-3-flash-lite"}),
        "lite": ("flash-lite", {"gemini-flash-lite", "gemini-3-flash-lite"}),
        "gemini-flash-lite": ("flash-lite", {"gemini-flash-lite", "gemini-3-flash-lite"}),
        "gemini-3-flash-lite": ("flash-lite", {"gemini-flash-lite", "gemini-3-flash-lite"}),
    }
    family = families.get(normalized)
    registry = getattr(client, "_model_registry", None)
    resolver = getattr(client, "_resolve_model_by_name", None)
    if family and isinstance(registry, dict) and callable(resolver):
        try:
            selected = resolver(family[0])
        except ValueError:
            selected = None
        if selected is not None and any(selected is item for item in registry.values()):
            name = getattr(selected, "model_name", "")
            if isinstance(name, str) and name.lower() in family[1]:
                return selected, f"discovered model {name}"

    # Never let the account's implicit default replace an explicit Pro/Argon
    # choice. The shared client boundary must still discover this exact family
    # before it can dispatch the named request.
    return resolved, resolved


def format_research_query(query: str, requested_model: str, model_note: str) -> str:
    return (
        f"{query}\n\n"
        "Deep Research request metadata:\n"
        f"- Requested MCP model alias: {requested_model}\n"
        f"- Transport model selection: {model_note}\n"
        "The client enforces this model selection; report capability limits "
        "rather than claiming that this text selected a different model."
    )


def phase_timeout(timeout_seconds: int) -> int:
    return max(30, timeout_seconds)


class null_scope:
    """Context manager fallback for test doubles and older clients."""

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def _input_rejected(message: str) -> DomainResult[LongOperationData]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
        details={"service": "research"},
    )


def _capability_unavailable(request: ResearchRequest) -> DomainResult[LongOperationData]:
    return DomainResult.failure(
        DomainErrorCode.CAPABILITY_UNAVAILABLE,
        "The Gemini Web client does not expose the native Deep Research start API.",
        data=LongOperationData(operation=request.operation, state=OperationState.UNAVAILABLE),
        suggested_action="Update gemini-webapi, or use the compatibility gemini_deep_research tool.",
        operation_state=OperationState.UNAVAILABLE,
        verification_status="capability_not_available",
        details={"service": "research"},
    )


def _failure_from_exception(
    error: BaseException,
    request: ResearchRequest,
    *,
    operation_id: str | None = None,
    plan: Any = None,
    chat: Any = None,
    failure_phase: str | None = None,
) -> DomainResult[LongOperationData]:
    details = {"service": "research", "exception_type": type(error).__name__,
               "failure_phase": failure_phase or "unknown", "failure_reason": _safe_research_failure_reason(error)}
    if isinstance(error, ResearchCleanupPreconditionError):
        data = research_operation_data(
            OperationState.FAILED, operation=request.operation, operation_id=operation_id, plan=plan, chat=chat,
        )
        return DomainResult.failure(
            DomainErrorCode.CAPABILITY_UNAVAILABLE,
            "Research confirmation was not sent because a chat ID or durable cleanup policy was unavailable.",
            data=data, operation_state=OperationState.FAILED, verification_status="start_not_attempted",
            details=details,
        )
    classified = result_from_exception(error, logger=_safe_failure_logger, operation=request.operation)
    failure = classified.error
    assert failure is not None
    data = research_operation_data(
        classified.meta.operation_state,
        operation=request.operation,
        operation_id=operation_id,
        plan=plan,
        chat=chat,
    )
    return DomainResult.failure(
        failure.code,
        failure.message,
        data=data,
        retryable=failure.retryable,
        suggested_action=failure.suggested_action,
        operation_state=classified.meta.operation_state,
        request_id=classified.meta.request_id,
        diagnostic_id=classified.meta.diagnostic_id,
        verification_status=classified.meta.verification_status,
        details=details,
    )


def _safe_research_failure_reason(error: BaseException) -> str:
    """Classify known SDK errors without returning previews, identifiers, or exception text."""
    text = str(error)
    prefixes = (
        ("Gemini did not return a deep research plan.", "plan_missing"),
        ("Failed to generate contents. No output data found in response.", "no_output"),
        ("Gemini returned no usable output for deep research.", "research_no_output"),
        ("Permission denied. Account status:", "account_restricted"),
        ("The connection to Gemini was lost while generating the response, and recovery timed out.",
         "generation_recovery_timeout"),
        ("Cannot poll deep research: the plan carries no chat id.", "research_chat_id_missing"),
    )
    for prefix, code in prefixes:
        if text.startswith(prefix):
            return code
    if " is not available for use. Account status:" in text:
        return "model_unavailable"
    return "unknown"
