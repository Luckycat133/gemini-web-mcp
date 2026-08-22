"""Focused ``gemini_assist_mcp`` surface for task-first Gemini assistance.

This is the dedicated assistance server from the focused-product topology.
It exposes a small, deterministic catalog and delegates every request to the
shared :class:`~src.services.chat.ChatService` instead of keeping a second
copy of request construction, parsing, or persistence.
"""

import logging
from typing import Optional

from .. import __version__
from ..adapters import (
    MCPServer,
    TextContent,
    attach_domain_result,
    domain_error_boundary,
    domain_failure_text,
    domain_text,
)
from ..client_wrapper import (
    cleanup_due_remote_chats,
    create_session,
    get_gemini_client,
    init_cookie_manager_integration,
    initialize_client,
    lookup_session,
    schedule_remote_chat_cleanup,
    schedule_remote_chat_cleanup_from_response,
    send_session_message,
    send_session_message_stream,
)
from ..constants import normalize_model_alias, resolve_model_name
from ..domain import DomainErrorCode, DomainResult
from ..services import (
    ChatRequest,
    ChatService,
    ChatServiceDependencies,
    GroundingState,
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
from ..tools.annotations import MUTATES_REMOTE
from ..tools.utils import parse_response

logger = logging.getLogger(__name__)

SERVER_NAME = "gemini_assist_mcp"

mcp = MCPServer(
    name=SERVER_NAME,
    version=__version__,
    instructions=f"""
# gemini-assist (v{__version__})

Focused assistance surface: extend an agent with Gemini second opinions,
critique, code/design review, grounded web search, and visual or mixed-input
understanding. It deliberately exposes no history, Cookie, Scheduled, Gem,
Prompt, or cleanup tools.

## Tools
- **gemini_ask**: one-shot text question with optional context
- **gemini_search**: grounded web search with observed source evidence;
  grounding_state is grounded only when source URLs were observed
- **gemini_understand_image**: understand one image from a local path or
  image URI, with the input image keeping a stable artifact identity
- **gemini_understand**: typed mixed-input understanding over text, image
  path/URI, file path/URI, and URL inputs; each input keeps its id and a
  per-input outcome (accepted, analyzed, skipped, or failed)

## Models
- flash-lite, flash (default), thinking, pro
- thinking_level: standard or extended (gemini_ask)
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
        )
    )


_chat_service = _build_chat_service()
_search_service = SearchService(_chat_service)
_understand_service = UnderstandService(_chat_service)


def _invalid_argument(message: str) -> DomainResult[None]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
    )


def _compose_ask_message(prompt: str, context: Optional[str]) -> str:
    """Combine the prompt with optional context for one shared-chat request."""
    message = prompt.strip()
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
) -> list[TextContent]:
    """Ask Gemini one focused question and return the answer with backend evidence.

    Use this for second opinions, critique, or code/design review on material
    supplied in ``prompt`` and optional ``context``. The structured result
    carries requested/effective/observed model evidence and chat lifecycle
    metadata alongside the answer text.
    """
    if not prompt.strip():
        return domain_text(
            _invalid_argument("prompt must not be blank."),
            "Error: prompt must not be blank.",
        )

    result = await _chat_service.generate(
        ChatRequest(
            message=_compose_ask_message(prompt, context),
            model=model,
            thinking_level=thinking_level,
            cleanup_source="gemini_ask",
            include_gem_argument=False,
            include_temporary_argument=False,
        )
    )
    assert result.data is not None
    return attach_domain_result(
        parse_response(result.data.response, model),
        result,
        data={
            "requested_model": result.data.requested_model,
            "effective_model": result.data.effective_model,
            "observed_backend": observed_backend_from_response(result.data.response),
            "lifecycle": result.data.lifecycle,
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
            lines.append(f"{index}. {source.url}" + (f" — {title}" if title else ""))
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
    max_results: int = 8,
    model: str = "flash",
) -> list[TextContent]:
    """Search the web through Gemini and return the answer with observed source evidence.

    Use this for current-web questions whose answer should name its sources.
    The structured result carries ``grounding_state`` (grounded, answer_only,
    unavailable, or failed), the observed ``sources`` list, and an
    ``observed_at`` timestamp. An answer without observed source evidence is
    reported as ``answer_only`` and is never labeled grounded.
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
    """Understand one image and return the analysis tied to that image.

    Use this for screenshots, photos, diagrams, and other single-image tasks.
    ``image`` accepts a local image file path or an http(s) image URI; local
    images ride the shared chat upload workflow. The structured result
    carries the input image artifact identity, the per-input outcome, and
    requested/effective/observed model evidence alongside the analysis text.
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
    inputs: list[UnderstandInput],
    model: str = "flash",
    thinking_level: str = "standard",
) -> list[TextContent]:
    """Understand mixed text, image, file, and URL inputs in one request.

    Each input is one typed object — ``{"id", "kind", ...}`` — instead of one
    overloaded string: ``kind=text`` carries ``text``, ``kind=image`` accepts a
    local ``path`` or an http(s) URI, ``kind=file`` accepts a local ``path`` or
    an http(s) URI, and ``kind=url`` carries an absolute ``url``. Every input
    keeps its ``id``; the structured result records a per-input outcome
    (``accepted``, ``analyzed``, ``skipped``, or ``failed``) so no input is
    silently dropped, plus the synthesized analysis.
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


def main() -> None:
    """Run the gemini_assist_mcp stdio server."""
    logger.info("Starting %s (v%s)", SERVER_NAME, __version__)
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()
