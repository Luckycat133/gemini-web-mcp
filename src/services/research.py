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
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..constants import resolve_model_name
from ..domain import (
    ArtifactKind,
    ArtifactVerificationStatus,
    DomainErrorCode,
    DomainResult,
    LongOperationData,
    OperationState,
    result_from_exception,
)
from ..infrastructure.state_store import OperationRecord
from ..thinking_client import ThinkingLevelGeminiClient, client_generation_once
from .artifacts import artifact_from_local_path
from .operations import OperationContext, OperationService, get_operation_service

logger = logging.getLogger(__name__)

RESEARCH_CLEANUP_SOURCE = "gemini_research"
DEFAULT_RESEARCH_TIMEOUT_SECONDS = 600
RESEARCH_START_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class ResearchServiceDependencies:
    """Client and cleanup seams the research service binds to at call time."""

    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    cleanup_due_remote_chats: Callable[[Any], Awaitable[int]]
    schedule_chat_cleanup: Callable[..., Any]
    resolve_model: Callable[[str], str]


@dataclass(frozen=True)
class ResearchRequest:
    """One asynchronous Deep Research start request.

    ``retain_chat`` defaults to ``True`` so the started research chat — and with
    it the final report — stays recoverable through the preserved upstream chat
    identifier.
    """

    query: str
    model: str = "flash"
    thinking_level: str = "extended"
    timeout_seconds: int = DEFAULT_RESEARCH_TIMEOUT_SECONDS
    retain_chat: bool = True
    delete_after_seconds: int | None = None
    cleanup_source: str = RESEARCH_CLEANUP_SOURCE
    operation: str = "gemini_research"
    output_dir: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ResearchExecution:
    result: DomainResult[LongOperationData]
    upstream_result: Any = None


class ResearchService:
    """Start Deep Research asynchronously and return a typed operation handle.

    The service reuses the same native plan/start workflow as the compatibility
    ``gemini_deep_research`` tool (fresh research chat, transport model
    resolution, capability-probe recovery, start-with-recovery), then saves
    reports before cleanup. Start returns an opaque high-entropy operation ID
    before provider work. The shared metadata repository owns continuation;
    later clients only read the previously observed source.
    """

    def __init__(self, dependencies: ResearchServiceDependencies, operations: OperationService | None = None):
        self._dependencies = dependencies
        self._operations = operations or get_operation_service()
        self._operations.register_recovery("research", self.recover)

    async def start(self, request: ResearchRequest) -> DomainResult[LongOperationData]:
        query = request.query.strip()
        if not query:
            return _input_rejected("query must not be blank.")

        async def runner(context: OperationContext) -> DomainResult[LongOperationData]:
            try:
                client = await self._prepare_client()
            except Exception as error:
                return _failure_from_exception(error, request, operation_id=context.operation_id)
            if not has_native_research_api(client):
                return _capability_unavailable(request, operation_id=context.operation_id)
            execution = await self.execute_native(client, request, context, wait_for_completion=False)
            return execution.result

        return await self._operations.start("research", runner, recovery=self.recover,
                                            idempotency_key=request.idempotency_key,
                                            output_dir=self._destination(request), retain_chat=request.retain_chat,
                                            delete_after_seconds=request.delete_after_seconds)

    def reserve(self, request: ResearchRequest) -> tuple[DomainResult[LongOperationData], OperationContext | None]:
        result, context, created = self._operations.reserve("research", output_dir=self._destination(request),
                                                            idempotency_key=request.idempotency_key,
                                                            retain_chat=request.retain_chat,
                                                            delete_after_seconds=request.delete_after_seconds)
        return result, context if created else None

    @staticmethod
    def _destination(request: ResearchRequest) -> str:
        return str(Path(request.output_dir or "generated_reports").expanduser().absolute())

    async def status(self, operation_id: str) -> DomainResult[LongOperationData]:
        return await self._operations.status(operation_id)

    async def result(self, operation_id: str) -> DomainResult[LongOperationData]:
        return await self._operations.result(operation_id)

    async def cancel(self, operation_id: str) -> DomainResult[LongOperationData]:
        # The installed SDK exposes no provider cancellation proof. The
        # shared owner therefore reports cancel_requested and retains source.
        return await self._operations.cancel(operation_id)

    async def execute_fallback(self, client: Any, request: ResearchRequest) -> ResearchExecution:
        """Compatibility-only plan fallback, with the same durable ownership."""
        reserved, context = self.reserve(request)
        if context is None:
            return ResearchExecution(reserved)
        async with self._operations.execution(context) as owns:
            if not owns:
                return ResearchExecution(reserved)
            response = None
            chat = None
            try:
                _model, note = resolve_deep_research_transport_model(request.model)
                model = self._dependencies.resolve_model(request.model)
                if isinstance(client, ThinkingLevelGeminiClient):
                    chat = start_fresh_research_chat(client, model, on_chat_observed=context.observe_chat_sync)
                with client_generation_once(client):
                    response = await await_before_deadline(client.generate_content(
                        format_research_query(request.query, request.model, note),
                        model=model, deep_research=True,
                        thinking_level=request.thinking_level, timeout=request.timeout_seconds,
                        **({"chat": chat} if chat is not None else {}),
                    ), timeout=request.timeout_seconds)
                result = research_domain_result(research_operation_data(OperationState.RUNNING,
                    operation=request.operation, operation_id=context.operation_id, chat=chat, response=response,
                    latest_upstream_state="running"))
            except Exception as error:
                result = _failure_from_exception(error, request, operation_id=context.operation_id, chat=chat)
            finally:
                cid = research_chat_id(chat=chat, response=response)
                if cid:
                    context.observe_chat_sync(cid)
                    self._dependencies.schedule_chat_cleanup(cid, retain_chat=True,
                                                              delete_after_seconds=request.delete_after_seconds,
                                                              source=request.cleanup_source)
            self._operations.finish(context, result)
            return ResearchExecution(result, response)

    async def execute_native(
        self, client: Any, request: ResearchRequest, context: OperationContext, *,
        wait_for_completion: bool, poll_interval: float = 10,
        fetch_report: Callable[[Any, str], Awaitable[Any]] | None = None,
        request_report: Callable[[Any], Awaitable[Any]] | None = None,
    ) -> ResearchExecution:
        """One shared plan/start/wait owner; adapters only render this outcome."""
        async with self._operations.execution(context) as owns:
            if not owns:
                record = self._operations.store.operations.get(context.scope_id, context.operation_id)
                return ResearchExecution(self._operations._render(record) if record is not None else self._operations._not_found())
            return await self._execute_native(client, request, context, wait_for_completion=wait_for_completion,
                                              poll_interval=poll_interval, fetch_report=fetch_report, request_report=request_report)

    async def _execute_native(
        self, client: Any, request: ResearchRequest, context: OperationContext, *, wait_for_completion: bool,
        poll_interval: float, fetch_report: Callable[[Any, str], Awaitable[Any]] | None,
        request_report: Callable[[Any], Awaitable[Any]] | None,
    ) -> ResearchExecution:
        research_model, model_note = resolve_deep_research_transport_model(request.model)
        start = await run_deep_research_start_phase(
            client,
            query=request.query.strip(),
            requested_model=request.model,
            resolved_model=self._dependencies.resolve_model(request.model),
            research_model=research_model,
            model_note=model_note,
            thinking_level=request.thinking_level,
            timeout_seconds=request.timeout_seconds,
            operation=request.operation,
            operation_id=context.operation_id,
            schedule_chat_cleanup=self._dependencies.schedule_chat_cleanup,
            retain_chat=request.retain_chat,
            delete_after_seconds=request.delete_after_seconds,
            cleanup_source=request.cleanup_source,
            observe_locator=lambda cid, provider: context.observe_chat_sync(cid, provider_operation_id=provider),
            observe_provider_locator=context.observe_provider_operation_sync,
        )
        if start.timed_out is not None:
            self._operations.finish(context, start.timed_out)
            return ResearchExecution(start.timed_out)
        if start.error is not None:
            result = _failure_from_exception(
                start.error,
                request,
                operation_id=context.operation_id,
                plan=start.plan,
                chat=start.chat,
            )
            self._operations.finish(context, result)
            return ResearchExecution(result)
        state = operation_state_from_upstream(getattr(start.start_output, "state", None))
        provider_id = nonempty_identifier(getattr(start.plan, "research_id", None))
        if provider_id:
            context.observe_provider_operation_sync(provider_id)
        upstream_result = SimpleNamespace(plan=start.plan, start_output=start.start_output, final_output=None,
                                          statuses=[SimpleNamespace(state=state.value, done=False,
                                                                    notes=["caller requested start-only execution"])],
                                          done=False, poll_count=0)
        if wait_for_completion:
            try:
                if getattr(start.plan, "research_id", None):
                    upstream_result = await await_before_deadline(client.wait_for_deep_research(
                        start.plan, poll_interval=poll_interval, timeout=request.timeout_seconds,
                    ), timeout=request.timeout_seconds)
                elif fetch_report is not None and request_report is not None:
                    upstream_result = await wait_for_deep_research_by_chat(
                        client, start.plan, start.chat, start.start_output, poll_interval=poll_interval,
                        timeout=request.timeout_seconds, fetch_report=fetch_report, request_report=request_report,
                    )
                else:
                    raise asyncio.TimeoutError
                upstream_result.start_output = start.start_output
                statuses = list(getattr(upstream_result, "statuses", ()) or ())
                last_state = operation_state_from_upstream(statuses[-1] if statuses else None)
                state = OperationState.COMPLETED if getattr(upstream_result, "done", False) else (
                    last_state if last_state in {OperationState.FAILED, OperationState.CANCELLED, OperationState.UNAVAILABLE}
                    else OperationState.TIMED_OUT
                )
            except asyncio.TimeoutError:
                result = research_timed_out_result(operation=request.operation, operation_id=context.operation_id,
                                                   plan=start.plan, chat=start.chat, start_output=start.start_output)
                self._operations.finish(context, result)
                return ResearchExecution(result)
            except Exception as error:
                result = _failure_from_exception(error, request, operation_id=context.operation_id,
                                                 plan=start.plan, chat=start.chat)
                self._operations.finish(context, result)
                return ResearchExecution(result)
        data = research_operation_data(
            state,
            operation=request.operation,
            operation_id=context.operation_id,
            plan=start.plan,
            chat=start.chat,
            upstream_result=upstream_result,
            latest_upstream_state=state.value if not wait_for_completion else None,
        )
        result = research_domain_result(data)
        final_output = getattr(upstream_result, "final_output", None) or start.start_output
        body = research_report_body(final_output, provider_completed=bool(getattr(upstream_result, "done", False)))
        if body:
            result = await self._save_report(context, data, body, client)
        elif state == OperationState.COMPLETED:
            result = research_domain_result(replace(data, state=OperationState.RUNNING, report_available=False))
        self._operations.finish(context, result)
        return ResearchExecution(result, upstream_result)

    async def _save_report(self, context: OperationContext, data: LongOperationData, body: str, client: Any) -> DomainResult[LongOperationData]:
        failure_data = replace(data, state=OperationState.FAILED, report_available=False)
        record = self._operations.store.operations.get(context.scope_id, context.operation_id)
        if record is None or not record.output_dir:
            return DomainResult.failure(DomainErrorCode.ARTIFACT_SAVE_FAILED, "Research destination is unavailable.",
                                        data=failure_data, verification_status="report_not_saved")
        path = Path(record.output_dir) / f"{context.operation_id}.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # A deterministic opaque filename makes cross-client result reads
            # converge. Existing content must match; never overwrite a caller's
            # unrelated file in the approved destination.
            fd, temporary_name = tempfile.mkstemp(prefix=f".{context.operation_id}.", dir=path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    output.write(body)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if path.read_text(encoding="utf-8") != body:
                        raise ValueError("Report destination already contains different content.") from None
            finally:
                temporary.unlink(missing_ok=True)
            if path.read_text(encoding="utf-8") != body:
                raise ValueError("Saved report did not match its source.")
            artifact = artifact_from_local_path(ArtifactKind.REPORT, str(path), source_chat_id=data.upstream_chat_id)
            if artifact.verification.status != ArtifactVerificationStatus.VERIFIED:
                raise ValueError("Saved report could not be verified.")
        except (OSError, ValueError):
            return DomainResult.failure(DomainErrorCode.ARTIFACT_SAVE_FAILED, "Research report could not be saved and verified; its source is retained.",
                                        data=failure_data, verification_status="report_not_saved")
        result = research_domain_result(replace(data, state=OperationState.COMPLETED, report_available=True, artifacts=(artifact,)))
        try:
            context.observe_artifacts_sync((artifact,), operation_state=OperationState.COMPLETED,
                                           verification_status="report_observed")
        except (ValueError, RuntimeError):
            return DomainResult.failure(DomainErrorCode.ARTIFACT_SAVE_FAILED, "The saved report locator could not be retained; its source remains available.",
                                        data=replace(failure_data, artifacts=(artifact,)), verification_status="locator_not_retained")
        if self._operations._scope() == context.scope_id:
            from ..client_wrapper import finalize_generated_chat_cleanup
            cleanup = await finalize_generated_chat_cleanup(SimpleNamespace(cid=data.upstream_chat_id), owns_chat=True,
                                                             retain_chat=record.retain_chat,
                                                             delete_after_seconds=record.delete_after_seconds,
                                                             source="research", client=client)
            result = replace(result, meta=replace(result.meta, details={**result.meta.details, "cleanup": cleanup}))
        return result

    async def recover(self, record: OperationRecord) -> DomainResult[LongOperationData]:
        """Read an existing report; never create a plan or send a message."""
        data = self._operations._data(record)
        if record.artifacts:
            verified = tuple(replace(artifact_from_local_path(ArtifactKind.REPORT, item.local_path,
                                                             source_chat_id=record.upstream_chat_id), id=item.id)
                             for item in record.artifacts if item.local_path)
            if verified and all(item.verification.status == ArtifactVerificationStatus.VERIFIED for item in verified):
                return research_domain_result(replace(data, state=OperationState.COMPLETED, report_available=True, artifacts=verified))
        if not record.upstream_chat_id:
            return DomainResult.success(replace(data, state=OperationState.RUNNING), operation_state=OperationState.RUNNING,
                                        verification_status="source_not_observed")
        client = await self._prepare_client()
        fetch = getattr(client, "fetch_latest_chat_response", None)
        if not callable(fetch):
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "The client cannot read the existing research source.",
                                        data=data, operation_state=OperationState.UNAVAILABLE, verification_status="readback_unavailable")
        # Current SDK supports a match callback so a later acknowledgement
        # cannot hide an earlier completed document in the same owned chat.
        import inspect
        supports_match = "match" in inspect.signature(fetch).parameters
        output = await fetch(record.upstream_chat_id, **({"limit": 10, "match": lambda value: bool(research_report_body(value))} if supports_match else {}))
        observed_cid = research_chat_id(response=output)
        if observed_cid and observed_cid != record.upstream_chat_id:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Research read-back returned a different source identity.",
                                        data=data, verification_status="source_identity_changed")
        body = research_report_body(output)
        if not body:
            state = operation_state_from_upstream(getattr(output, "state", None))
            if state == OperationState.COMPLETED or output is None or (
                upstream_state(output) is None and not is_research_start_message(getattr(output, "text", ""))
            ):
                state = record.state if record.state in {OperationState.QUEUED, OperationState.RUNNING, OperationState.CANCEL_REQUESTED,
                                                         OperationState.FAILED, OperationState.TIMED_OUT} else OperationState.RUNNING
            unresolved = replace(data, state=state, report_available=False)
            unresolved_result = research_domain_result(unresolved)
            return replace(unresolved_result, meta=replace(unresolved_result.meta, verification_status="completion_not_observed"))
        return await self._save_report(OperationContext(self._operations, record), data, body, client)

    async def _prepare_client(self) -> Any:
        client = self._dependencies.client_provider()
        await self._dependencies.client_initializer()
        await self._dependencies.cleanup_due_remote_chats(client)
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
    observe_locator: Callable[[str, str | None], None] | None = None,
    observe_provider_locator: Callable[[str], None] | None = None,
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
    try:
        chat = start_fresh_research_chat(
            client, research_model,
            on_chat_observed=(lambda cid: observe_locator(cid, None)) if observe_locator is not None else None,
        )
        scope = research_thinking_scope(client, research_model, resolved_model, thinking_level)
        with scope, client_generation_once(client):
            plan = await await_before_deadline(
                create_deep_research_plan(
                    client,
                    format_research_query(query, requested_model, model_note),
                    chat=chat,
                    model=research_model,
                ),
                timeout=phase_timeout(timeout_seconds),
            )
            chat_id = research_chat_id(plan=plan, chat=chat)
            provider_id = nonempty_identifier(getattr(plan, "research_id", None))
            if provider_id and observe_provider_locator is not None:
                observe_provider_locator(provider_id)
            if chat_id and observe_locator is not None:
                observe_locator(chat_id, provider_id)
            start_output = await start_deep_research_with_recovery(
                client,
                plan,
                chat,
                timeout=min(phase_timeout(timeout_seconds), RESEARCH_START_TIMEOUT_SECONDS),
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
                )
    except asyncio.TimeoutError:
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
        )
    except Exception as error:
        return DeepResearchStart(chat, plan, start_output, error=error)
    finally:
        # Preserve every observed fresh source, including a partial plan
        # failure. A report must be saved before applying disposal policy.
        chat_id = research_chat_id(plan=plan, chat=chat, response=start_output)
        if chat_id is not None:
            if observe_locator is not None:
                observe_locator(chat_id, nonempty_identifier(getattr(plan, "research_id", None)))
            schedule_chat_cleanup(
                chat_id,
                # A plan/start result still contains the only recoverable
                # report source. Apply the caller policy after local save.
                retain_chat=True,
                delete_after_seconds=delete_after_seconds,
                source=cleanup_source,
            )
    return DeepResearchStart(chat, plan, start_output)


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


def consume_finished_task(task: asyncio.Future[Any]) -> None:
    """Observe a detached task result so late completion cannot leak warnings."""
    try:
        task.result()
    except BaseException:
        pass


async def await_before_deadline(
    awaitable: Awaitable[Any],
    *,
    timeout: float,
) -> Any:
    """Await strictly until a deadline and never adopt a late completion.

    ``asyncio.wait_for`` can continue waiting when a child suppresses its
    cancellation.  Long-operation state must be final at the declared
    deadline, so this helper detaches and consumes any such late result.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait(
            {task},
            timeout=max(0.0, float(timeout)),
        )
    except BaseException:
        if not task.done():
            task.cancel()
        task.add_done_callback(consume_finished_task)
        raise

    if not done:
        task.cancel()
        task.add_done_callback(consume_finished_task)
        raise asyncio.TimeoutError
    return await task


def nonempty_identifier(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def research_chat_id(
    *,
    plan: Any = None,
    chat: Any = None,
    response: Any = None,
) -> str | None:
    for owner in (chat, plan, response):
        identifier = nonempty_identifier(getattr(owner, "observed_chat_id", None)) or nonempty_identifier(getattr(owner, "cid", None))
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
    if state in {"unavailable", "not_available"}:
        return OperationState.UNAVAILABLE
    return OperationState.RUNNING


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
    final_text = getattr(final_output, "text", "") if final_output else ""
    report_available = (
        state is OperationState.COMPLETED
        and isinstance(final_text, str)
        and bool(final_text.strip())
    )
    return LongOperationData(
        operation=operation,
        state=state,
        operation_id=operation_id,
        upstream_operation_id=upstream_operation_id,
        upstream_chat_id=chat_id,
        title=nonempty_identifier(getattr(plan, "title", None)),
        latest_upstream_state=latest_upstream_state,
        continuation_possible=bool(upstream_operation_id or chat_id),
        report_available=report_available,
        poll_count=max(0, poll_count),
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


async def create_deep_research_plan(client: Any, query: str, chat: Any, model: Any):
    try:
        return await client.create_deep_research_plan(query, chat=chat, model=model)
    except Exception as e:
        if not is_capability_probe_false_negative(e) or not all(
            hasattr(client, attr)
            for attr in ("_deep_research_preflight", "_collect_research_output")
        ):
            raise

        logger.warning("Deep Research capability probe failed, trying direct research request: %s", e)
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


def start_fresh_research_chat(client: Any, model: Any, *, on_chat_observed: Callable[[str], None] | None = None):
    """Create a chat that is not polluted by gemini_webapi's shared default metadata."""
    if isinstance(client, ThinkingLevelGeminiClient):
        return client.start_owned_chat(model=model, on_chat_observed=on_chat_observed)
    chat = client.start_chat(model=model)
    for attr in ("cid", "rid", "rcid"):
        try:
            setattr(chat, attr, "")
        except Exception:
            logger.debug("Could not clear fresh research chat %s", attr)
    return chat


async def start_deep_research_with_recovery(client: Any, plan: Any, chat: Any, timeout: float):
    deadline = asyncio.get_running_loop().time() + max(0.0, timeout)
    try:
        return await await_before_deadline(
            client.start_deep_research(plan, chat=chat),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("Deep Research start timed out; inspecting recoverable state within the deadline")
        latest = None
        cid = getattr(chat, "cid", None) or getattr(plan, "cid", None)
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining > 0 and cid and hasattr(client, "fetch_latest_chat_response"):
            try:
                latest = await await_before_deadline(
                    client.fetch_latest_chat_response(cid), timeout=remaining,
                )
            except asyncio.TimeoutError:
                pass
        return latest or SimpleNamespace(text="", timeout_during_start=True)


def is_research_start_message(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in (
        "i'm on it", "i’ll let you know", "i'll let you know", "research is finished",
        "while i'm researching", "leave this chat", "i've finished the research",
        "i have finished the research", "我已经完成了研究", "研究完成后", "我这就开始",
    ))


def is_research_completion_message(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in (
        "i've finished the research", "i have finished the research", "我已经完成了研究",
    ))


def _followup_report_observed(output: Any) -> bool:
    """Accept a report response, rather than a changed acknowledgement or refusal."""
    text = getattr(output, "text", "")
    if not isinstance(text, str) or not text.strip() or is_research_start_message(text):
        return False
    if operation_state_from_upstream(getattr(output, "state", None)) in {
        OperationState.FAILED, OperationState.CANCELLED, OperationState.UNAVAILABLE,
    }:
        return False
    if nonempty_identifier(getattr(output, "report_id", None)):
        return True
    if getattr(output, "sources", None) or getattr(output, "deep_research_sources", None):
        return True
    # A pasted report may lose provider metadata. Require report structure and
    # an observed source trail after the explicit completion observation.
    return bool(
        len(text.strip()) >= 1000
        and re.search(r"^#{1,6}\s+\S", text, re.MULTILINE)
        and re.search(r"https?://[^\s<>]+", text)
    )


def research_report_body(output: Any, *, provider_completed: bool = False) -> str | None:
    """Require a typed document or a report-bearing completion observation."""
    document = getattr(output, "deep_research_document", None)
    content = getattr(document, "content", None)
    if isinstance(content, str) and content.strip():
        return content.strip()
    text = getattr(output, "text", None)
    if not isinstance(text, str) or not text.strip() or is_research_start_message(text):
        return None
    if re.match(r"(?is)^\s*(?:you(?:'ve| have) reached|daily (?:usage )?limit|quota exceeded|已达到|您已达到|无法完成|我无法完成)", text):
        return None
    if provider_completed or _followup_report_observed(output):
        return text.strip()
    return None


async def wait_for_deep_research_by_chat(
    client: Any,
    plan: Any,
    chat: Any,
    start_output: Any,
    *,
    poll_interval: float,
    timeout: float,
    fetch_report: Callable[[Any, str], Awaitable[Any]],
    request_report: Callable[[Any], Awaitable[Any]],
) -> Any:
    """Poll a no-ID operation under one deadline, requiring report evidence."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    cid = research_chat_id(plan=plan, chat=chat, response=start_output)
    checks = 0
    followup_requested = False

    def result(state: OperationState, note: str, output: Any = None) -> Any:
        return SimpleNamespace(
            plan=plan, start_output=start_output, final_output=output,
            statuses=[SimpleNamespace(state=state.value, done=state is OperationState.COMPLETED, notes=[note])],
            done=state is OperationState.COMPLETED, poll_count=checks,
        )

    async def before_deadline(awaitable: Awaitable[Any]) -> Any:
        return await await_before_deadline(awaitable, timeout=max(0.0, deadline - loop.time()))

    try:
        while loop.time() < deadline:
            if cid and hasattr(client, "fetch_latest_chat_response"):
                latest = await before_deadline(client.fetch_latest_chat_response(cid))
                checks += 1
                text = getattr(latest, "text", "") or ""
                state = operation_state_from_upstream(getattr(latest, "state", None))
                if state in {OperationState.FAILED, OperationState.CANCELLED, OperationState.UNAVAILABLE}:
                    return result(state, "upstream research reached a non-success terminal state")
                if state is OperationState.COMPLETED or is_research_completion_message(text):
                    if _followup_report_observed(latest):
                        return result(OperationState.COMPLETED, "report observed in completed chat response", latest)
                    report = await before_deadline(fetch_report(client, cid))
                    report_text = getattr(report, "text", "") if report is not None else ""
                    if isinstance(report_text, str) and report_text.strip():
                        return result(
                            OperationState.COMPLETED,
                            f"retrieved immersive report from raw chat payload after {checks} checks", report,
                        )
                    if not followup_requested:
                        # A report follow-up is still a generation mutation.
                        # Mark it before awaiting so timeout/uncertain failure
                        # cannot repost it on the next history poll.
                        followup_requested = True
                        with client_generation_once(client):
                            report = await before_deadline(request_report(chat))
                        if _followup_report_observed(report):
                            return result(
                                OperationState.COMPLETED,
                                f"retrieved completed report by follow-up after {checks} checks", report,
                            )
            else:
                checks += 1
            remaining = deadline - loop.time()
            if remaining > 0:
                await asyncio.sleep(min(max(0.0, poll_interval), remaining))
    except asyncio.TimeoutError:
        pass
    return result(
        OperationState.RUNNING,
        f"research_id was not present in Gemini's plan; checked chat history {checks} times; report not observed",
    )


def is_capability_probe_false_negative(error: Exception) -> bool:
    text = str(error)
    return "appears not eligible for deep research" in text and "Failed: []" in text


def is_default_deep_research_transport(model: Any) -> bool:
    return getattr(model, "model_name", None) == "unspecified" or model == "unspecified"


def resolve_deep_research_transport_model(requested_model: str) -> tuple[Any, str]:
    """Return the Gemini Web transport model that is stable for Deep Research."""
    try:
        from gemini_webapi.constants import Model
    except ImportError:
        return resolve_model_name(requested_model), resolve_model_name(requested_model)

    resolved = resolve_model_name(requested_model)
    if requested_model in {"", None}:
        requested_model = "flash"

    if str(requested_model).strip().lower() in {"flash-lite", "lite", "flash", "fast", "pro", "thinking"}:
        return (
            Model.UNSPECIFIED,
            (
                "Gemini Web default Deep Research mode "
                f"(requested {requested_model}; explicit model header {resolved} is unstable for this workflow)"
            ),
        )
    return resolved, resolved


def format_research_query(query: str, requested_model: str, model_note: str) -> str:
    return (
        f"{query}\n\n"
        "Deep Research request metadata:\n"
        f"- Requested MCP model alias: {requested_model}\n"
        f"- Transport model selection: {model_note}\n"
        "If Gemini Web allows model-specific Deep Research, use the requested alias; "
        "otherwise proceed with the account's default Deep Research mode and state that limitation."
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


def _capability_unavailable(request: ResearchRequest, *, operation_id: str | None = None) -> DomainResult[LongOperationData]:
    return DomainResult.failure(
        DomainErrorCode.CAPABILITY_UNAVAILABLE,
        "The Gemini Web client does not expose the native Deep Research start API.",
        data=LongOperationData(operation=request.operation, state=OperationState.UNAVAILABLE, operation_id=operation_id),
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
) -> DomainResult[LongOperationData]:
    classified = result_from_exception(error, logger=logger, operation=request.operation)
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
        details={"service": "research"},
    )
