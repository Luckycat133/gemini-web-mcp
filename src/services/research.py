"""Shared Deep Research application service for assistance surfaces.

The service owns the asynchronous Deep Research start workflow — fresh research
chat, transport model resolution, plan creation with capability-probe recovery,
and start-with-recovery — below the MCP presentation layers. ``start`` never
waits for the final report: it returns a typed
:class:`~src.domain.LongOperationData` handle immediately, and recoverability
rides on the preserved upstream identifiers instead of any connection-local
MCP state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from ..constants import resolve_model_name
from ..domain import (
    DomainErrorCode,
    DomainResult,
    LongOperationData,
    OperationState,
    new_operation_id,
    result_from_exception,
)

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

    async def start(self, request: ResearchRequest) -> DomainResult[LongOperationData]:
        query = request.query.strip()
        if not query:
            return _input_rejected("query must not be blank.")

        try:
            client = await self._prepare_client()
        except Exception as error:
            return _failure_from_exception(error, request)

        if not has_native_research_api(client):
            return _capability_unavailable(request)

        research_model, model_note = resolve_deep_research_transport_model(request.model)
        chat = start_fresh_research_chat(client, research_model)
        scope = self._thinking_scope(client, research_model, request)
        operation_id = new_operation_id()
        plan = None
        start_output = None
        try:
            with scope:
                plan = await await_before_deadline(
                    create_deep_research_plan(
                        client,
                        format_research_query(query, request.model, model_note),
                        chat=chat,
                        model=research_model,
                    ),
                    timeout=phase_timeout(request.timeout_seconds),
                )
                start_output = await start_deep_research_with_recovery(
                    client,
                    plan,
                    chat,
                    timeout=min(phase_timeout(request.timeout_seconds), RESEARCH_START_TIMEOUT_SECONDS),
                )
        except asyncio.TimeoutError:
            data = research_operation_data(
                OperationState.TIMED_OUT,
                operation=request.operation,
                operation_id=operation_id,
                plan=plan,
                chat=chat,
                latest_upstream_state=upstream_state(start_output),
            )
            return research_domain_result(data)
        except Exception as error:
            return _failure_from_exception(
                error,
                request,
                operation_id=operation_id,
                plan=plan,
                chat=chat,
            )
        finally:
            if plan is not None:
                self._dependencies.schedule_chat_cleanup(
                    research_chat_id(plan=plan, chat=chat),
                    retain_chat=request.retain_chat,
                    delete_after_seconds=request.delete_after_seconds,
                    source=request.cleanup_source,
                )

        state = operation_state_from_upstream(getattr(start_output, "state", None))
        data = research_operation_data(
            state,
            operation=request.operation,
            operation_id=operation_id,
            plan=plan,
            chat=chat,
            latest_upstream_state=state.value,
            poll_count=0,
        )
        return research_domain_result(data)

    async def _prepare_client(self) -> Any:
        client = self._dependencies.client_provider()
        await self._dependencies.client_initializer()
        await self._dependencies.cleanup_due_remote_chats(client)
        return client

    def _thinking_scope(
        self,
        client: Any,
        research_model: Any,
        request: ResearchRequest,
    ) -> Any:
        if is_default_deep_research_transport(research_model):
            return null_scope()
        thinking_scope = getattr(client, "thinking_scope", None)
        if thinking_scope is None:
            return null_scope()
        return thinking_scope(self._dependencies.resolve_model(request.model), request.thinking_level)


def has_native_research_api(client: Any) -> bool:
    """Report whether the client exposes the native plan/start/wait API."""
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
        OperationState.COMPLETED: "report_observed",
        OperationState.QUEUED: "upstream_queued",
        OperationState.RUNNING: "upstream_running",
    }.get(data.state, "upstream_state_observed")
    return DomainResult.success(
        data,
        operation_state=data.state,
        verification_status=verification_status,
        details=details,
    )


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


def start_fresh_research_chat(client: Any, model: Any):
    """Create a chat that is not polluted by gemini_webapi's shared default metadata."""
    chat = client.start_chat(model=model)
    for attr in ("cid", "rid", "rcid"):
        try:
            setattr(chat, attr, "")
        except Exception:
            logger.debug("Could not clear fresh research chat %s", attr)
    return chat


async def start_deep_research_with_recovery(client: Any, plan: Any, chat: Any, timeout: int):
    try:
        return await await_before_deadline(
            client.start_deep_research(plan, chat=chat),
            timeout=timeout,
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
