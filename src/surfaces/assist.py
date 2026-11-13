"""Focused ``gemini_assist_mcp`` surface for task-first Gemini assistance.

This is the dedicated assistance server from the focused-product topology.
It exposes a small, deterministic catalog and delegates every request to the
shared ChatService/SearchService/UnderstandService/ResearchService instead of
keeping a second copy of request construction, parsing, or persistence.
"""

import logging
from typing import Any, Literal, Optional

from pydantic import Field

from .. import __version__
from ..adapters import (
    MCPServer,
    TextContent,
    append_artifact_block,
    attach_domain_result,
    domain_error_boundary,
    domain_failure_text,
    domain_text,
)
from ..client_wrapper import (
    cleanup_due_remote_chats,
    create_session,
    finalize_remote_chat_cleanup_from_response,
    finalize_remote_chat_cleanup,
    get_gemini_client,
    init_startup_infrastructure,
    initialize_client,
    lookup_session,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
    send_session_message,
    send_session_message_stream,
    server_lifespan,
)
from ..constants import normalize_model_alias, resolve_model_name
from ..domain import DomainErrorCode, DomainResult, LongOperationData, OperationState
from ..services.search import response_evidence
from ..services import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_RESEARCH_TIMEOUT_SECONDS,
    MAX_UNDERSTAND_INPUTS,
    ChatRequest,
    ChatService,
    ChatServiceDependencies,
    GroundingState,
    ResearchRequest,
    ResearchService,
    ResearchServiceDependencies,
    SearchOperationData,
    SearchRequest,
    SearchService,
    UnderstandImageRequest,
    UnderstandInput,
    UnderstandOperationData,
    UnderstandRequest,
    UnderstandService,
    observed_backend_from_response,
)
from ..tools.annotations import DESTRUCTIVE_REMOTE, MUTATES_REMOTE
from ..adapters.artifacts import omit_saved_preview
from ..tools.utils import parse_response

logger = logging.getLogger(__name__)

SERVER_NAME = "gemini_assist_mcp"

mcp = MCPServer(
    lifespan=server_lifespan,
    name=SERVER_NAME,
    version=__version__,
    instructions=f"""
# gemini-assist (v{__version__})

Focused assistance surface: extend an agent with Gemini second opinions,
critique, code/design review, grounded web search, visual or mixed-input
understanding, and asynchronous Deep Research starts. It deliberately exposes
no history, Cookie, Scheduled, Gem, Prompt, or cleanup tools.

## Tools
- **gemini_ask**: ask naturally, including requests to use an available app such as Google Maps
- **gemini_search**: grounded web search with observed source evidence;
  grounding_state is grounded only when source URLs were observed
- **gemini_understand_image**: understand one image from a local path or
  image URI, with the input image keeping a stable artifact identity
- **gemini_understand**: typed mixed-input understanding over text, image
  path/URI, file path/URI, and URL inputs (max 16); each input keeps its id
  and a per-input outcome (accepted, analyzed, skipped, or failed), where
  analyzed means the completed analysis acknowledged that input
- **gemini_research**: start one Deep Research run asynchronously and return
  an opaque operation handle with the upstream operation/chat IDs preserved;
  unfinished work gets a finite cleanup grace period; report retrieval cleans
  the chat unless the caller explicitly selected retain_chat=true

## Models
- flash (default), argon for writing or especially difficult reasoning, pro when explicitly needed
- All models support image understanding and reasoning; start search and multimodal work with Flash
- flash-lite is runtime-gated to fresh, same-account basic Flash quota exhaustion; it has no selectable thinking level
- thinking_level: low, medium or high (standard/extended remain low/high aliases)
""",
)


def _build_chat_service() -> ChatService:
    """Bind the shared chat service to the shared client/session seams."""
    return ChatService(
        ChatServiceDependencies(
            client_provider=lambda: get_gemini_client(),
            client_initializer=lambda: initialize_client(),
            cleanup_due_remote_chats=lambda client: cleanup_due_remote_chats(client),
            create_session=lambda *args, **kwargs: create_session(*args, **kwargs),
            lookup_session=lambda session_id: lookup_session(session_id),
            send_session_message=lambda *args, **kwargs: send_session_message(*args, **kwargs),
            send_session_message_stream=lambda *args, **kwargs: send_session_message_stream(*args, **kwargs),
            schedule_response_cleanup=lambda *args, **kwargs: schedule_remote_chat_cleanup_from_response(
                *args,
                **kwargs,
            ),
            schedule_chat_cleanup=lambda *args, **kwargs: schedule_remote_chat_cleanup(*args, **kwargs),
            normalize_model=lambda model: normalize_model_alias(model),
            resolve_model=lambda model: resolve_model_name(model),
            finalize_response_cleanup=lambda response, *, retain_chat, delete_after_seconds, source, expected_account: (
                finalize_remote_chat_cleanup_from_response(
                    response, retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                    source=source, client=get_gemini_client(), expected_account=expected_account,
                )
            ),
        )
    )


_chat_service = _build_chat_service()
_search_service = SearchService(_chat_service)
_understand_service = UnderstandService(_chat_service)


def _build_research_service() -> ResearchService:
    """Bind the shared research service to the shared client/session seams."""
    return ResearchService(
        ResearchServiceDependencies(
            client_provider=lambda: get_gemini_client(),
            client_initializer=lambda: initialize_client(),
            cleanup_due_remote_chats=lambda client: cleanup_due_remote_chats(client),
            schedule_chat_cleanup=lambda *args, **kwargs: schedule_remote_chat_cleanup(*args, **kwargs),
            resolve_model=lambda model: resolve_model_name(model),
            finalize_chat_cleanup=lambda *args, **kwargs: finalize_remote_chat_cleanup(*args, **kwargs),
        )
    )


_research_service = _build_research_service()


def _invalid_argument(message: str) -> DomainResult[None]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
    )


def _compose_ask_message(prompt: str, context: Optional[str]) -> str:
    """Combine the prompt with optional context for one shared-chat request."""
    message = prompt
    stripped_context = (context or "").strip()
    if stripped_context:
        message = f"{message}\n\n---\n\nContext:\n{stripped_context}"
    return message


@mcp.tool(annotations=MUTATES_REMOTE)
@domain_error_boundary("gemini_ask", logger)
async def gemini_ask(
    prompt: str,
    context: Optional[str] = None,
    model: str = "flash",
    thinking_level: str = "standard",
    retain_chat: bool = False,
    delete_after_seconds: int | None = None,
) -> list[TextContent]:
    """Ask Gemini Chat for answers, writing, web research, or direct app requests.

    Name the app in the prompt. Use observed sources for grounding and verify
    app effects independently. Thinking accepts low/medium/high or standard/extended.
    """
    if not prompt.strip():
        return domain_text(
            _invalid_argument("prompt must not be blank."),
            "Error: prompt must not be blank.",
        )

    result = await _chat_service.generate_stream(
        ChatRequest(
            message=_compose_ask_message(prompt, context),
            model=model,
            thinking_level=thinking_level,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            cleanup_source="gemini_ask",
            include_gem_argument=False,
            include_temporary_argument=False,
        )
    )
    if not result.ok:
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    assert result.data is not None
    evidence = response_evidence(result.data.responses, result.data.response,
                                 stream_text=result.data.stream_text, observed_at=result.meta.observed_at)
    final_text = getattr(result.data.response, "text", "")
    display_text = final_text if isinstance(final_text, str) and final_text else result.data.stream_text
    return attach_domain_result(
        parse_response(result.data.response, model, text_override=display_text),
        result,
        data={
            "requested_model": result.data.requested_model,
            "effective_model": result.data.effective_model,
            "observed_backend": observed_backend_from_response(result.data.response),
            "lifecycle": result.data.lifecycle,
            "answer": evidence.answer,
            "grounding_state": evidence.grounding_state,
            "sources": evidence.sources,
            "observed_source_count": evidence.observed_source_count,
            "observed_at": evidence.observed_at,
            "model_selection": result.data.model_selection,
        },
    )


def _render_search_result(data: SearchOperationData) -> list[TextContent]:
    """Present the grounded-search answer without contradicting structured state."""
    sections: list[str] = []
    answer = data.answer.strip()
    if answer:
        sections.append(answer)
    else:
        sections.append("No answer was returned for this search.")
    if data.sources:
        lines = ["Sources:"]
        for index, source in enumerate(data.sources, 1):
            title = (source.title or "").strip()
            role = " [retrieved; support unverified]" if source.role == "retrieved_only" else ""
            lines.append(f"{index}. {source.url}" + (f" — {title}" if title else "") + role)
        if data.observed_source_count is not None and data.observed_source_count > len(data.sources):
            lines.append(f"({data.observed_source_count - len(data.sources)} more sources observed)")
        sections.append("\n".join(lines))
    elif data.grounding_state == GroundingState.ANSWER_ONLY:
        sections.append("No source evidence was observed for this answer.")
    footer = f"Grounding state: {data.grounding_state.value}"
    if data.observed_at:
        footer += f"\nObserved at: {data.observed_at}"
    sections.append(footer)
    return [TextContent(type="text", text="\n\n".join(sections))]


@mcp.tool(annotations=MUTATES_REMOTE)
@domain_error_boundary("gemini_search", logger)
async def gemini_search(
    query: str,
    recency: Optional[str] = None,
    domains: Optional[list[str]] = None,
    language: Optional[str] = None,
    max_results: int = DEFAULT_MAX_RESULTS,
    model: str = "flash",
) -> list[TextContent]:
    """Search the web with Gemini. Returns an answer, observed sources and timestamp.

    grounding_state is grounded only with observed attribution or citation;
    other states are answer_only, unavailable and failed.
    """
    result = await _search_service.search(
        SearchRequest(
            query=query,
            recency=recency,
            domains=tuple(domains or ()),
            language=language,
            max_results=max_results,
            model=model,
        )
    )
    if not result.ok or result.data is None:
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    return attach_domain_result(
        _render_search_result(result.data),
        result,
        use_result_data=True,
    )


def _render_understand_result(data: UnderstandOperationData) -> list[TextContent]:
    """Present the understanding analysis and per-input outcomes together."""
    sections: list[str] = []
    analysis = data.analysis.strip()
    sections.append(analysis or "No analysis was returned for this request.")
    if data.inputs:
        lines = ["Inputs:"]
        for outcome in data.inputs:
            line = f"- [{outcome.id or '(missing id)'}] {outcome.kind}: {outcome.outcome.value}"
            if outcome.detail:
                line += f" ({outcome.detail})"
            lines.append(line)
        sections.append("\n".join(lines))
    return [TextContent(type="text", text="\n\n".join(sections))]


def _render_understand_response(result: DomainResult[UnderstandOperationData]) -> list[TextContent]:
    if not result.ok or result.data is None:
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    return attach_domain_result(
        _render_understand_result(result.data),
        result,
        use_result_data=True,
    )


@mcp.tool(annotations=MUTATES_REMOTE)
@domain_error_boundary("gemini_understand_image", logger)
async def gemini_understand_image(
    image: str,
    task: Optional[str] = None,
    model: str = "flash",
    thinking_level: str = "standard",
) -> list[TextContent]:
    """Analyze one screenshot, photo or diagram from a local path or HTTP(S) URL.

    Returns analysis, the input artifact and its outcome, and model evidence.
    """
    result = await _understand_service.understand_image(
        UnderstandImageRequest(
            image=image,
            task=task,
            model=model,
            thinking_level=thinking_level,
        )
    )
    return _render_understand_response(result)


@mcp.tool(annotations=MUTATES_REMOTE)
@domain_error_boundary("gemini_understand", logger)
async def gemini_understand(
    task: str,
    inputs: list[UnderstandInput] = Field(max_length=MAX_UNDERSTAND_INPUTS),
    model: str = "flash",
    thinking_level: str = "standard",
) -> list[TextContent]:
    """Analyze up to 16 mixed inputs with one Gemini request.

    Give each input an id and kind. Text uses text, image/file uses path or
    HTTP(S) uri, and url uses url. Outcomes are accepted, analyzed, skipped
    or failed. Analyzed requires a completed answer for that sole input or
    a reference to its [id] in a multi-input answer.
    """
    result = await _understand_service.understand(
        UnderstandRequest(
            task=task,
            inputs=tuple(inputs),
            model=model,
            thinking_level=thinking_level,
        )
    )
    return _render_understand_response(result)


def _render_research_start(data: LongOperationData) -> list[TextContent]:
    """Present the started research handle without contradicting structured state."""
    sections: list[str] = [f"Deep Research is {data.state.value}."]
    if data.title:
        sections.append(f"Title: {data.title}")
    if data.operation_id:
        sections.append(f"Operation handle: {data.operation_id}")
    if data.upstream_operation_id:
        sections.append(f"Upstream research ID: {data.upstream_operation_id}")
    if data.upstream_chat_id:
        sections.append(f"Upstream chat ID: {data.upstream_chat_id}")
    if data.state in {OperationState.QUEUED, OperationState.RUNNING}:
        sections.append("This call returned immediately and did not wait for the final report.")
    elif data.state is OperationState.ACCEPTED:
        sections.append("The task was accepted; upstream progress has not yet been verified.")
    elif data.state is OperationState.PARTIAL:
        sections.append("No ready research report was observed; completion is not verified.")
    elif data.state is OperationState.COMPLETED and not data.report_available:
        sections.append("The upstream run completed, but this start call did not return a report.")
    return [TextContent(type="text", text="\n".join(sections))]


@mcp.tool(annotations=DESTRUCTIVE_REMOTE)
@domain_error_boundary("gemini_research", logger)
async def gemini_research(
    query: str = "",
    model: str = "flash",
    thinking_level: str = "standard",
    timeout_seconds: int = DEFAULT_RESEARCH_TIMEOUT_SECONDS,
    retain_chat: bool = False,
    delete_after_seconds: Optional[int] = None,
    operation_id: Optional[str] = None,
    action: Literal["start", "status", "result"] = "start",
    background: bool = True,
    include_text: bool = True,
) -> list[TextContent]:
    """Start AI Plus Deep Research for a durable report from multiple sources.

    Runs in background by default. Use action=status/result with the original
    operation_id after reconnecting. background=false waits for startup. Timeout
    bounds planning/startup (30s minimum, 120s start cap); the report runs longer.
    Optionally supply op_ plus 32 random lowercase hex digits before starting.
    For seven days the same database reuses that handle and its saved settings.
    Recovery confirms the original plan at most once. Without an observed chat
    ID, the handle prevents duplicate start but cannot recover the remote plan.
    Do not reuse expired handles. Set retain_chat=true to keep the conversation.
    For result, include_text=false omits a preview when the verified file is available.
    """
    if action in {"status", "result"}:
        result: DomainResult[Any]
        if not operation_id or not operation_id.strip():
            result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "operation_id is required.")
        else:
            from ..infrastructure.operation_store import OperationStore
            from ..services.operations import OperationService
            service = OperationService(OperationStore(), get_gemini_client, initialize_client,
                                       finalize_remote_chat_cleanup)
            result = await (service.get_status(operation_id) if action == "status" else service.get_result(operation_id))
        omitted = False
        if action == "result" and not include_text:
            result, omitted = omit_saved_preview(result)
        data = result.data
        text = f"Operation {data.operation_id}: {data.state.value}." if result.ok and data else domain_failure_text(result)
        if data and data.report:
            text += f"\n\n{data.report}"
        elif omitted:
            text += "\nFull report in verified local artifact."
        content = domain_text(result, text, use_result_data=True)
        return append_artifact_block(content, data.artifacts) if data and data.artifacts else content
    request = ResearchRequest(
            query=query,
            model=model,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            operation_id=operation_id,
        )
    if not query.strip():
        result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "query must not be blank.",
                                      verification_status="input_rejected")
    elif background:
        from ..services.background import submit_research
        result = await submit_research(request)
    else:
        result = await _research_service.start(request)
    if not result.ok or result.data is None:
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    return attach_domain_result(
        _render_research_start(result.data),
        result,
        use_result_data=True,
    )


def register_assist_tools(server: MCPServer, *, notebook_context: bool = False) -> None:
    """Expose the same five handlers on another thin MCP surface."""
    handlers: tuple[Any, ...]
    if notebook_context:
        @server.tool(name="gemini_ask", annotations=MUTATES_REMOTE)
        @domain_error_boundary("gemini_ask", logger)
        async def ask_with_notebooks(
            prompt: str, context: str | None = None, model: str = "flash", thinking_level: str = "standard",
            notebook_ids: list[str] | None = None, timeout_seconds: int = 600,
            retain_chat: bool = False, delete_after_seconds: int | None = None,
            operation_id: str | None = None,
        ) -> list[TextContent]:
            """Ask Gemini Chat for answers, writing, web research or named app requests.

            notebook_ids from gemini_notebooks starts ordinary Chat in background.
            Read the original operation_id for the saved answer and cleanup.
            Without Notebook sources the answer returns inline. Use gemini_run_task
            for delegated ongoing work or browser operation. Thinking accepts
            low/medium/high or the standard/extended aliases.
            """
            if notebook_ids:
                from ..services.background import submit_notebook_chat
                result = await submit_notebook_chat({
                    "prompt": _compose_ask_message(prompt, context), "model": model,
                    "thinking_level": thinking_level, "notebook_ids": notebook_ids,
                    "timeout_seconds": timeout_seconds, "retain_chat": retain_chat,
                    "delete_after_seconds": delete_after_seconds,
                }, operation_id=operation_id)
                text = (f"Notebook chat accepted. Operation handle: {result.data.operation_id}"
                        if result.ok and result.data else domain_failure_text(result))
                return domain_text(result, text, use_result_data=True)
            if operation_id is not None:
                invalid = _invalid_argument("operation_id requires notebook_ids for a background chat.")
                return domain_text(invalid, domain_failure_text(invalid), use_result_data=True)
            return await gemini_ask(prompt, context, model, thinking_level,
                                    retain_chat=retain_chat, delete_after_seconds=delete_after_seconds)
        handlers = (gemini_search, gemini_understand_image, gemini_understand)
    else:
        handlers = (gemini_ask, gemini_search, gemini_understand_image, gemini_understand)
    for handler in handlers:
        server.tool(annotations=MUTATES_REMOTE)(handler)
    server.tool(annotations=DESTRUCTIVE_REMOTE)(gemini_research)


def main() -> None:
    """Run the gemini_assist_mcp stdio server."""
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting %s (v%s)", SERVER_NAME, __version__)
    init_startup_infrastructure()
    mcp.run()


if __name__ == "__main__":
    main()
