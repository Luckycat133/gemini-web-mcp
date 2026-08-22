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
critique, and code/design review. It deliberately exposes no history,
Cookie, Scheduled, Gem, Prompt, or cleanup tools.

## Tools
- **gemini_ask**: one-shot text question with optional context

## Models
- flash-lite, flash (default), thinking, pro
- thinking_level: standard or extended
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


def main() -> None:
    """Run the gemini_assist_mcp stdio server."""
    logger.info("Starting %s (v%s)", SERVER_NAME, __version__)
    init_cookie_manager_integration()
    mcp.run()


if __name__ == "__main__":
    main()
