"""Focused ``gemini_assist_mcp`` surface for task-first Gemini assistance.

This is the dedicated assistance server from the focused-product topology.
It exposes a small, deterministic catalog and delegates every request to the
shared ChatService/SearchService/UnderstandService/ResearchService instead of
keeping a second copy of request construction, parsing, or persistence.
"""

import logging
from pathlib import Path
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlsplit

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
from ..domain import DomainErrorCode, DomainResult, LongOperationData, OperationState, result_from_exception
from ..services.search import response_evidence
from ..services import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_RESEARCH_TIMEOUT_SECONDS,
    MAX_UNDERSTAND_INPUTS,
    ChatOperationData,
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
    SessionMessageRequest,
    StartSessionRequest,
    UnderstandImageRequest,
    UnderstandInput,
    UnderstandInputKind,
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


def _render_ask_response(
    result: DomainResult[ChatOperationData], model: str, *, session_id: str | None = None,
    show_session_id: bool = False,
) -> list[TextContent]:
    if not result.ok:
        if session_id is None:
            return domain_text(result, domain_failure_text(result), use_result_data=True)
        data: dict[str, Any] = {"session_id": session_id}
        if result.data is not None:
            data["lifecycle"] = result.data.lifecycle
        text = domain_failure_text(result)
        if show_session_id:
            text += f"\nSession handle: {session_id}"
        return domain_text(result, text, data=data)
    assert result.data is not None
    evidence = response_evidence(result.data.responses, result.data.response,
                                 stream_text=result.data.stream_text, observed_at=result.meta.observed_at)
    final_text = getattr(result.data.response, "text", "")
    display_text = final_text if isinstance(final_text, str) and final_text else result.data.stream_text
    data = {
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
    }
    if session_id is not None:
        data["session_id"] = session_id
    content = parse_response(result.data.response, model, text_override=display_text)
    if show_session_id:
        content.append(TextContent(type="text", text=f"Session handle: {session_id}"))
    return attach_domain_result(content, result, data=data)


@mcp.tool(annotations=MUTATES_REMOTE)
@domain_error_boundary("gemini_ask", logger)
async def gemini_ask(
    prompt: str,
    context: Optional[str] = None,
    model: str = "flash",
    thinking_level: str = "standard",
    retain_chat: bool = False,
    delete_after_seconds: int | None = None,
    gem_id: str | None = None,
    learning_mode: str | None = None,
) -> list[TextContent]:
    """Ask Gemini Chat for answers, writing, web research, or direct app requests.

    Name the app in the prompt. Use observed sources for grounding and verify
    app effects independently. Thinking accepts low/medium/high or standard/extended.
    Supports optional gem_id (from `gemini-web-cli gems list`) and guided
    learning_mode (guided_learning/guided, canvas, interactive_quiz/quiz,
    flashcards, practice_test, study_guide).
    """
    if not prompt.strip():
        return domain_text(
            _invalid_argument("prompt must not be blank."),
            "Error: prompt must not be blank.",
        )
    if gem_id is not None and not gem_id.strip():
        return domain_text(
            _invalid_argument("gem_id must not be blank when provided."),
            "Error: gem_id must not be blank when provided.",
        )
    cleaned_gem = gem_id.strip() if gem_id is not None else None

    result = await _chat_service.generate_stream(
        ChatRequest(
            message=_compose_ask_message(prompt, context),
            model=model,
            thinking_level=thinking_level,
            learning_mode=learning_mode,
            gem_id=cleaned_gem,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            cleanup_source="gemini_ask",
            include_gem_argument=bool(cleaned_gem),
            include_temporary_argument=False,
        )
    )
    return _render_ask_response(result, model)


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


async def _ask_notebook(
    message: str, *, model: str, thinking_level: str, notebook_ids: list[str],
    timeout_seconds: int, retain_chat: bool, delete_after_seconds: int | None, operation_id: str | None,
) -> list[TextContent]:
    from ..services.background import submit_notebook_chat

    result = await submit_notebook_chat({
        "prompt": message, "model": model, "thinking_level": thinking_level,
        "notebook_ids": notebook_ids, "timeout_seconds": timeout_seconds,
        "retain_chat": retain_chat, "delete_after_seconds": delete_after_seconds,
    }, operation_id=operation_id)
    text = (f"Notebook chat accepted. Operation handle: {result.data.operation_id}"
            if result.ok and result.data else domain_failure_text(result))
    return domain_text(result, text, use_result_data=True)


def _reject_ask(message: str) -> list[TextContent]:
    invalid = _invalid_argument(message)
    return domain_text(invalid, domain_failure_text(invalid), use_result_data=True)


async def _ask_session(
    message: str, *, session_id: str | None, model: str, thinking_level: str,
    retain_chat: bool | None, delete_after_seconds: int | None, recover_only: bool,
    gem_id: str | None = None, learning_mode: str | None = None,
) -> list[TextContent]:
    created = session_id is None
    if session_id is None:
        cleaned_gem = gem_id.strip() if gem_id is not None else None
        opened = await _chat_service.start_session(StartSessionRequest(
            model=model, thinking_level=thinking_level, learning_mode=learning_mode,
            gem_id=cleaned_gem, retain_chat=bool(retain_chat),
            delete_after_seconds=delete_after_seconds, include_gem_argument=bool(cleaned_gem),
        ))
        if not opened.ok:
            return domain_text(opened, domain_failure_text(opened), use_result_data=True)
        assert opened.data is not None and opened.data.session_id is not None
        session_id = opened.data.session_id
    try:
        result = await _chat_service.send_session_stream(SessionMessageRequest(
            session_id=session_id, message=message, recover_only=recover_only,
            learning_mode=learning_mode if created else None,
            retain_chat=None if created else retain_chat,
            delete_after_seconds=None if created else delete_after_seconds,
            include_temporary=False, cleanup_source="gemini_ask",
        ))
    except Exception as error:
        # The first call already allocated a handle. Preserve it for the
        # caller even if sending fails after creating the session.
        result = result_from_exception(error, logger=logger, operation="gemini_ask")
    display_model = result.data.requested_model if result.data is not None else model
    return _render_ask_response(result, display_model, session_id=session_id, show_session_id=created)


async def _handle_research_report_action(
    *,
    operation_id: str,
    report_action: Literal[
        "list_actions", "webpage", "infographic", "quiz", "flashcards", "audio_overview", "custom_app",
    ],
    output_dir: str,
) -> list[TextContent]:
    from ..domain import ArtifactResultData, ArtifactState
    from ..services import (
        artifact_exception_result,
        artifact_from_local_path,
        artifact_result,
        artifact_save_failure_result,
    )
    from ..services.research_reports import resolve_research_report
    from ..tools.research import (
        _create_research_report_artifact,
        _fetch_deep_research_immersive_report,
        _format_research_report_actions,
        _format_research_report_artifact,
        _research_artifact_kind,
        _research_report_actions_payload,
    )

    source, unavailable = await resolve_research_report(
        chat_id=None,
        operation_id=operation_id,
        client_provider=get_gemini_client,
        client_initializer=initialize_client,
        fetch_report=_fetch_deep_research_immersive_report,
    )
    if source is None:
        assert unavailable is not None
        return domain_text(
            unavailable,
            domain_failure_text(unavailable)
            if not unavailable.ok
            else "No verified local report is available yet; fetch the completed operation result first.",
            use_result_data=True,
        )
    if not source.account_matches():
        return domain_text(
            DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Report account changed."),
            "Report account changed.",
            use_result_data=True,
        )
    if report_action == "list_actions":
        payload = _research_report_actions_payload(source.output, source.chat_id)
        if source.operation_id:
            payload.update(operation_id=source.operation_id, report_source="verified_local_artifact")
        result = DomainResult.success(payload, verification_status="research_artifact_verified")
        return domain_text(result, _format_research_report_actions(payload), use_result_data=True)
    chat_id = source.chat_id
    try:
        artifact = _create_research_report_artifact(
            report_output=source.output,
            artifact_type=report_action,
            chat_id=chat_id,
            output_dir=output_dir,
        )
        if source.operation_id:
            artifact.update(operation_id=source.operation_id, report_source="verified_local_artifact")
    except OSError as error:
        data = ArtifactResultData(
            state=ArtifactState.FAILED,
            request_model=report_action,
            effective_backend="MCP local renderer",
            observed_backend="filesystem",
            source_chat_id=chat_id,
            media_type="research_report",
        )
        return domain_text(
            artifact_save_failure_result(error, data, logger=logger, operation="gemini_research"),
            "Research report artifact creation failed. Check the output directory and retry.",
            use_result_data=True,
        )
    except Exception as error:
        data = ArtifactResultData(
            state=ArtifactState.FAILED,
            request_model=report_action,
            effective_backend="MCP local renderer",
            source_chat_id=chat_id,
            media_type="research_report",
        )
        return domain_text(
            artifact_exception_result(error, data, logger=logger, operation="gemini_research"),
            "Research report artifact creation failed.",
            use_result_data=True,
        )
    if not source.account_matches():
        return domain_text(
            DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "Report account changed."),
            "Report account changed.",
            use_result_data=True,
        )
    typed_artifact = artifact_from_local_path(
        _research_artifact_kind(artifact["path"]),
        artifact["path"],
        title=artifact["title"],
        source_chat_id=chat_id,
        request_model=report_action,
        effective_backend="MCP local renderer",
        observed_backend="filesystem",
    )
    data = ArtifactResultData(
        state=typed_artifact.state,
        artifacts=(typed_artifact,),
        request_model=report_action,
        effective_backend="MCP local renderer",
        observed_backend="filesystem",
        source_chat_id=chat_id,
        media_type="research_report",
    )
    save_failures = ("post_write_verification",) if typed_artifact.state == ArtifactState.FAILED else ()
    artifact_domain_result = artifact_result(data, save_failures=save_failures)
    text = _format_research_report_artifact(artifact)
    if typed_artifact.state == ArtifactState.FAILED:
        text = f"Research report artifact could not be verified.\n\n{text}"
    content = append_artifact_block([TextContent(type="text", text=text)], data.artifacts)
    return attach_domain_result(content, artifact_domain_result, use_result_data=True)


_LEAN_MODEL = Literal["hydrogen", "neon", "carbon", "auto"]
_LEAN_THINKING = Literal["low", "medium", "high"]
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".avif", ".svg"})


def _normalize_understand_inputs(items: list[str | UnderstandInput]) -> tuple[UnderstandInput, ...]:
    normalized: list[UnderstandInput] = []
    for idx, item in enumerate(items, 1):
        if isinstance(item, UnderstandInput):
            normalized.append(item)
            continue
        raw = item.strip()
        input_id = f"input_{idx}"
        if raw.startswith(("http://", "https://")):
            suffix = Path(urlsplit(raw).path).suffix.lower()
            kind = UnderstandInputKind.IMAGE if suffix in _IMAGE_EXTS else UnderstandInputKind.URL
            normalized.append(UnderstandInput(id=input_id, kind=kind, url=raw))
        elif raw:
            suffix = Path(raw).suffix.lower()
            if suffix in _IMAGE_EXTS:
                normalized.append(UnderstandInput(id=input_id, kind=UnderstandInputKind.IMAGE, path=raw))
            elif "/" in raw or "\\" in raw or raw.startswith(("~", ".")) or Path(raw).expanduser().exists():
                normalized.append(UnderstandInput(id=input_id, kind=UnderstandInputKind.FILE, path=raw))
            else:
                normalized.append(UnderstandInput(id=input_id, kind=UnderstandInputKind.TEXT, text=raw))
        else:
            normalized.append(UnderstandInput(id=input_id, kind=UnderstandInputKind.TEXT, text=""))
    return tuple(normalized)


def register_lean_assist_tools(server: MCPServer) -> None:
    @server.tool(name="ask", annotations=MUTATES_REMOTE)
    @domain_error_boundary("ask", logger)
    async def ask(
        prompt: str = "",
        context: str | None = None,
        model: _LEAN_MODEL | None = None,
        thinking_level: _LEAN_THINKING | None = None,
        gem_id: str | None = None,
        learning_mode: Literal[
            "guided_learning", "canvas", "quiz", "flashcards", "practice_test", "study_guide",
        ] | None = None,
        notebook_ids: list[str] | None = None,
        start_session: bool = False,
        session_id: str | None = None,
        recover_only: bool = False,
        timeout_seconds: int = 600,
        retain_chat: bool | None = None,
        delete_after_seconds: int | None = None,
        operation_id: str | None = None,
    ) -> list[TextContent]:
        """Chat, web Q&A, Gem, Canvas, guided learning, multi-turn sessions, or Notebook analysis.

        Defaults to model="hydrogen" and thinking_level="medium". For images/files/URLs use
        `understand`; for background tasks use `task`. Pass `start_session=true` to open a session,
        then pass only `session_id` to continue. Chats auto-clean unless `retain_chat=true`.
        """
        in_session = start_session or session_id is not None or recover_only
        if notebook_ids and in_session:
            return _reject_ask("Choose only one of notebook_ids or a multi-turn session.")
        if gem_id is not None and not gem_id.strip():
            return _reject_ask("gem_id must not be blank when provided.")
        if (gem_id is not None or learning_mode is not None) and (notebook_ids or recover_only):
            return _reject_ask("gem_id and learning_mode apply to single-turn chats or start_session=True.")
        if (gem_id is not None or learning_mode is not None) and session_id is not None:
            return _reject_ask("gem_id and learning_mode are set when starting a session, not when continuing session_id.")
        if not notebook_ids and (operation_id is not None or timeout_seconds != 600):
            return _reject_ask("operation_id and a custom timeout_seconds require notebook_ids.")
        if start_session and session_id is not None:
            return _reject_ask("Use start_session for a new session or session_id to continue.")
        if session_id is not None and (not session_id.strip() or model is not None or thinking_level is not None):
            return _reject_ask("Provide a nonblank session_id and inherit its model and thinking level.")
        if recover_only:
            if session_id is None or prompt.strip() or context or retain_chat is not None or delete_after_seconds is not None:
                return _reject_ask("For recovery, pass only session_id and recover_only.")
        elif not prompt.strip():
            return _reject_ask("prompt must not be blank.")

        resolved_model = "hydrogen" if model is None else model
        resolved_thinking = "medium" if thinking_level is None else thinking_level
        if in_session:
            return await _ask_session(
                _compose_ask_message(prompt, context), session_id=session_id, model=resolved_model,
                thinking_level=resolved_thinking, retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds, recover_only=recover_only,
                gem_id=gem_id, learning_mode=learning_mode,
            )
        if notebook_ids:
            return await _ask_notebook(
                _compose_ask_message(prompt, context), model=resolved_model, thinking_level=resolved_thinking,
                notebook_ids=notebook_ids, timeout_seconds=timeout_seconds, retain_chat=bool(retain_chat),
                delete_after_seconds=delete_after_seconds, operation_id=operation_id,
            )
        return await gemini_ask(
            prompt, context, resolved_model, resolved_thinking,
            retain_chat=bool(retain_chat), delete_after_seconds=delete_after_seconds,
            gem_id=gem_id, learning_mode=learning_mode,
        )

    @server.tool(name="understand", annotations=MUTATES_REMOTE)
    @domain_error_boundary("understand", logger)
    async def understand_lean(
        task: str,
        inputs: Annotated[list[str | UnderstandInput], Field(min_length=1, max_length=MAX_UNDERSTAND_INPUTS)],
        model: _LEAN_MODEL = "hydrogen",
        thinking_level: _LEAN_THINKING = "medium",
        retain_chat: bool = False,
    ) -> list[TextContent]:
        """Analyze images, local files, URLs, or text (up to 16 inputs).

        Accepts plain path/URL/text strings or `{id, kind, path|url|text}` objects.
        Defaults to model="hydrogen" and thinking_level="medium".
        """
        result = await _understand_service.understand(
            UnderstandRequest(
                task=task,
                inputs=_normalize_understand_inputs(inputs),
                model=model,
                thinking_level=thinking_level,
                cleanup_source="gemini_understand",
                retain_chat=retain_chat,
            )
        )
        return _render_understand_response(result)

    @server.tool(name="research", annotations=DESTRUCTIVE_REMOTE)
    async def research(
        query: str = "",
        model: _LEAN_MODEL = "hydrogen",
        thinking_level: _LEAN_THINKING = "medium",
        operation_id: str | None = None,
        report_action: Literal[
            "list_actions", "webpage", "infographic", "quiz", "flashcards", "audio_overview", "custom_app",
        ] | None = None,
        output_dir: str = "generated_media/research_artifacts",
        timeout_seconds: int = DEFAULT_RESEARCH_TIMEOUT_SECONDS,
        retain_chat: bool = False,
    ) -> list[TextContent]:
        """Start Deep Research asynchronously or derive artifacts from a completed handle.

        Pass `query` to start research and poll via `operation(operation_id=...)`.
        Pass `operation_id` + `report_action` (with blank query) to list or render local derivatives.
        """
        if report_action is not None:
            if query.strip() or retain_chat:
                return _reject_ask(
                    "When report_action is set, pass operation_id of a completed research job and omit query."
                )
            if operation_id is None or not operation_id.strip():
                return _reject_ask("report_action requires the operation_id of a completed research job.")
            return await _handle_research_report_action(
                operation_id=operation_id.strip(),
                report_action=report_action,
                output_dir=output_dir,
            )
        if output_dir != "generated_media/research_artifacts":
            return _reject_ask("output_dir only applies when report_action is set.")
        return await gemini_research(
            query, model, thinking_level, timeout_seconds, retain_chat, None,
            operation_id,
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
                return await _ask_notebook(
                    _compose_ask_message(prompt, context), model=model, thinking_level=thinking_level,
                    notebook_ids=notebook_ids, timeout_seconds=timeout_seconds, retain_chat=retain_chat,
                    delete_after_seconds=delete_after_seconds, operation_id=operation_id,
                )
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
