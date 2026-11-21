"""Gemini functionality: conversation, understanding, research and creation."""

from __future__ import annotations

import logging
from typing import Any

from .. import __version__
from ..adapters import MCPServer, TextContent, domain_failure_text, domain_text
from ..adapters.registration import selected_tools
from ..client_wrapper import init_startup_infrastructure, server_lifespan
from ..domain import DomainErrorCode, DomainResult
from ..services.background import submit_media
from ..services.media_storage import _valid_media_filename
from ..tools.annotations import MUTATES_REMOTE
from ..tools.task_generation import register_task_generation_tools
from ..tools.chat import register_chat_tools
from ..tools.operations import register_operation_tools
from ..tools.research import register_research_tools
from ..tools.utils import validate_optional_image_path
from .assist import register_assist_tools
from .catalogs import FUNCTION_TOOLS

logger = logging.getLogger(__name__)
mcp = MCPServer(name="Gemini functions", version=__version__, lifespan=server_lifespan, instructions="""
Default to gemini_ask for Chat, including complex questions, current web,
Notebook analysis and direct app requests. Name available apps naturally.
Web and tools need no enable switch. Flash is the default. Use Argon for
writing or difficult reasoning when available. Lite requires fresh same-account
basic Flash quota exhaustion and locks thinking strength.
Use gemini_run_task for explicit Task or delegated ongoing work, tracked progress
or browser operation. Choose by workflow even for background runs. Check app
availability in the chosen mode. Task uses Flash or Argon. Keep the original
handle for status and results. Task and Chat schedules are separate.
Use observed grounding and verify external app changes independently of prose.
""")

register_assist_tools(selected_tools(mcp, set(FUNCTION_TOOLS)), notebook_context=True)
register_chat_tools(selected_tools(mcp, {
    "gemini_start_chat", "gemini_send_message", "gemini_list_sessions", "gemini_reset_session",
}))
register_operation_tools(mcp)
register_task_generation_tools(mcp)
register_research_tools(selected_tools(mcp, {
    "gemini_list_research_report_actions", "gemini_create_from_research_report",
}))


async def _submit(
    *, prompt: str, media_type: str, model: str, thinking_level: str, image_path: str | None,
    output_dir: str | None, filename: str | None, timeout_seconds: int | None,
    retain_chat: bool, delete_after_seconds: int | None, operation_id: str | None,
    operation: str, edit: bool, duration_seconds: int | None = None,
) -> list[TextContent]:
    if not prompt.strip():
        result: DomainResult[Any] = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "prompt must not be blank.")
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    if not _valid_media_filename(filename):
        result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "filename must be a single nonempty file name.")
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    if edit and not image_path:
        result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "image_path is required for editing.")
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    valid, safe_path, message = validate_optional_image_path(image_path)
    if not valid:
        result = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, message or "Invalid image path.")
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    result = await submit_media({
        "prompt": prompt, "media_type": media_type, "model": model, "thinking_level": thinking_level,
        "image_path": safe_path, "output_dir": output_dir, "filename": filename,
        "timeout_seconds": timeout_seconds, "retain_chat": retain_chat,
        "delete_after_seconds": delete_after_seconds, "edit": edit, "duration_seconds": duration_seconds,
    }, operation=operation, operation_id=operation_id)
    handle = result.data.operation_id if result.data else None
    text = f"Media operation handle: {handle}" if result.ok and handle else domain_failure_text(result)
    return domain_text(result, text, use_result_data=True)


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_generate_image(
    prompt: str, model: str = "flash", thinking_level: str = "standard", output_dir: str | None = None,
    filename: str | None = None, timeout_seconds: int | None = None, retain_chat: bool = False,
    delete_after_seconds: int | None = None, operation_id: str | None = None,
) -> list[TextContent]:
    """Submit image generation in the background; query the returned operation handle."""
    return await _submit(prompt=prompt, media_type="image", model=model, thinking_level=thinking_level,
                         image_path=None, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_image", edit=False)


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_edit_image(
    image_path: str, prompt: str, model: str = "flash", thinking_level: str = "standard",
    output_dir: str | None = None, filename: str | None = None, timeout_seconds: int | None = None,
    retain_chat: bool = False, delete_after_seconds: int | None = None, operation_id: str | None = None,
) -> list[TextContent]:
    """Submit an image edit in the background; query the returned operation handle."""
    return await _submit(prompt=prompt, media_type="image", model=model, thinking_level=thinking_level,
                         image_path=image_path, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_edit_image", edit=True)


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_generate_video(
    prompt: str, model: str = "flash", thinking_level: str = "standard", image_path: str | None = None,
    output_dir: str | None = None, filename: str | None = None, timeout_seconds: int | None = None,
    retain_chat: bool = False, delete_after_seconds: int | None = None, operation_id: str | None = None,
    duration_seconds: int | None = None,
) -> list[TextContent]:
    """Generate a video in the background using the Gemini App's Create video mode by default.

    Use model="gemini-omni-1.2-flash" to explicitly call the paid Gemini API via
    GEMINI_API_KEY. Only this route accepts duration_seconds from 3 to 15.
    Poll the original operation_id for a verified MP4.
    """
    return await _submit(prompt=prompt, media_type="video", model=model, thinking_level=thinking_level,
                         image_path=image_path, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_video", edit=False,
                         duration_seconds=duration_seconds)


@mcp.tool(annotations=MUTATES_REMOTE)
async def gemini_generate_music(
    prompt: str, model: str = "flash", thinking_level: str = "standard", output_dir: str | None = None,
    filename: str | None = None, timeout_seconds: int | None = None, retain_chat: bool = False,
    delete_after_seconds: int | None = None, operation_id: str | None = None,
) -> list[TextContent]:
    """Submit music generation in the background; query the returned operation handle."""
    return await _submit(prompt=prompt, media_type="music", model=model, thinking_level=thinking_level,
                         image_path=None, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_music", edit=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gemini functions (v%s)", __version__)
    init_startup_infrastructure()
    mcp.run()


if __name__ == "__main__":
    main()
