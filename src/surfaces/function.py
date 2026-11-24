"""Gemini functionality: conversation, understanding, research and creation."""

from __future__ import annotations

import logging
from typing import Any, Literal

from .. import __version__
from ..adapters import MCPServer, TextContent, domain_error_boundary, domain_failure_text, domain_text
from ..adapters.registration import selected_tools
from ..client_wrapper import init_startup_infrastructure, server_lifespan
from ..domain import DomainErrorCode, DomainResult
from ..services.background import submit_media
from ..services.media_generation import MusicDuration, MusicVocals, music_option_arguments, music_options_error
from ..services.media_inputs import image_workflow_options_error
from ..services.media_storage import _valid_media_filename
from ..tools.annotations import MUTATES_REMOTE
from ..tools.task_generation import register_task_generation_tools
from ..tools.operations import register_operation_tools
from ..tools.utils import validate_optional_image_path
from .assist import register_assist_tools, register_lean_assist_tools
from .catalogs import FUNCTION_FULL_TOOLS

logger = logging.getLogger(__name__)
_INSTRUCTIONS = """
Default to gemini_ask for Chat, including complex questions, current web,
Notebook analysis and direct app requests. Name available apps naturally.
Web and tools need no enable switch. The Flash compatibility default prefers
the discovered Hydrogen model. Explicit Neon and Carbon use exact discovered
selectors and verified plan availability. Lite requires fresh same-account
basic Flash quota exhaustion and locks thinking strength.
Use gemini_run_task for explicit Task or delegated ongoing work, tracked progress
or browser operation. Choose by workflow even for background runs. Check app
availability in the chosen mode. Task supports discovered Hydrogen, Neon and
Carbon according to the verified plan. Keep the original
handle for status and results. Task and Chat schedules are separate.
Use observed grounding and verify external app changes independently of prose.
"""


def _invalid_media(message: str) -> list[TextContent]:
    result: DomainResult[Any] = DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, message)
    return domain_text(result, domain_failure_text(result), use_result_data=True)


async def _submit(
    *, prompt: str, media_type: str, model: str | None, thinking_level: str, image_path: str | None,
    output_dir: str | None, filename: str | None, timeout_seconds: int | None,
    retain_chat: bool, delete_after_seconds: int | None, operation_id: str | None,
    operation: str, edit: bool, duration_seconds: int | None = None, aspect_ratio: str | None = None,
    image_paths: list[str] | None = None, source_operation_id: str | None = None,
    source_artifact_id: str | None = None,
    wait_seconds: int = 0,
    music_duration: MusicDuration | None = None, music_vocals: MusicVocals | None = None,
) -> list[TextContent]:
    if not prompt.strip():
        return _invalid_media("prompt must not be blank.")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        return _invalid_media("model must not be blank when provided.")
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= 120:
        return _invalid_media("wait_seconds must be a whole number from 0 through 120.")
    if not _valid_media_filename(filename):
        return _invalid_media("filename must be a single nonempty file name.")
    invalid = image_workflow_options_error(
        media_type=media_type, image_path=image_path, image_paths=image_paths,
        source_operation_id=source_operation_id, source_artifact_id=source_artifact_id,
    )
    if invalid:
        return _invalid_media(invalid)
    invalid = music_options_error(media_type=media_type, music_duration=music_duration, music_vocals=music_vocals)
    if invalid:
        return _invalid_media(invalid)
    if edit and not (image_path or image_paths or source_operation_id):
        return _invalid_media("Editing requires an image_path, image_paths, or source_operation_id.")
    valid, safe_path, message = validate_optional_image_path(image_path)
    if not valid:
        return _invalid_media(message or "Invalid image path.")
    result = await submit_media({
        "prompt": prompt, "media_type": media_type, "model": model, "thinking_level": thinking_level,
        "image_path": safe_path, "output_dir": output_dir, "filename": filename,
        "timeout_seconds": timeout_seconds, "retain_chat": retain_chat,
        "delete_after_seconds": delete_after_seconds, "edit": edit, "duration_seconds": duration_seconds,
        "aspect_ratio": aspect_ratio,
        "image_paths": image_paths, "source_operation_id": source_operation_id, "source_artifact_id": source_artifact_id,
        **music_option_arguments(music_duration, music_vocals),
    }, operation=operation, operation_id=operation_id)
    handle = result.data.operation_id if result.data else None
    if result.ok and handle and wait_seconds > 0:
        from ..tools.operations import _present, _service

        service = _service()
        store = getattr(service, "store", None)
        if store is not None and store.get(handle) is not None:
            waited = await service.get_result(handle, wait_seconds=wait_seconds)
            return _present(waited, include_text=False)
    text = f"Media operation handle: {handle}" if result.ok and handle else domain_failure_text(result)
    return domain_text(result, text, use_result_data=True)


@domain_error_boundary("gemini_create_media", logger)
async def gemini_create_media(
    kind: Literal["image", "edit_image", "video", "music"], prompt: str,
    model: str | None = None, thinking_level: str = "standard", image_path: str | None = None,
    output_dir: str | None = None, filename: str | None = None, timeout_seconds: int | None = None,
    retain_chat: bool = False, delete_after_seconds: int | None = None, operation_id: str | None = None,
    duration_seconds: int | None = None,
    aspect_ratio: Literal["16:9", "9:16"] | None = None,
    image_paths: list[str] | None = None, source_operation_id: str | None = None,
    source_artifact_id: str | None = None,
    wait_seconds: int = 120,
    music_duration: MusicDuration | None = None,
    music_vocals: MusicVocals | None = None,
) -> list[TextContent]:
    """Submit an image, image edit, video or music job in the background.

    For image generation and editing, image_paths supplies ordered local
    references. Edit an earlier result by passing source_operation_id and, for
    multiple saved images, source_artifact_id. The saved image is attached
    before any local references in the new Web chat. image_path is the legacy
    single-file option for edit_image and video. Video uses
    Gemini Web unless model="gemini-omni-1.2-flash" explicitly selects the paid
    Gemini API. Web videos accept duration_seconds 10 or 15 and aspect_ratio
    16:9 or 9:16. The explicit API route accepts 3 to 15 seconds. Web music has
    optional music_duration (short, standard) and music_vocals (instrumental,
    vocals, auto). Keep the operation_id for status and verified artifact
    retrieval. Chats clean up by default. wait_seconds=120 waits on the
    original operation. Use zero to receive its background handle immediately.
    """
    if image_path is not None and kind not in {"edit_image", "video"}:
        return _invalid_media("image_path is only available for edit_image or video.")
    edit = kind == "edit_image"
    return await _submit(
        prompt=prompt, media_type="image" if edit else kind, model=model, thinking_level=thinking_level,
        image_path=image_path, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
        retain_chat=retain_chat, delete_after_seconds=delete_after_seconds, operation_id=operation_id,
        operation="gemini_edit_image" if edit else f"gemini_generate_{kind}", edit=edit,
        duration_seconds=duration_seconds, aspect_ratio=aspect_ratio,
        image_paths=image_paths, source_operation_id=source_operation_id, source_artifact_id=source_artifact_id,
        wait_seconds=wait_seconds,
        music_duration=music_duration, music_vocals=music_vocals,
    )


async def gemini_generate_image(
    prompt: str, model: str = "flash", thinking_level: str = "standard", output_dir: str | None = None,
    filename: str | None = None, timeout_seconds: int | None = None, retain_chat: bool = False,
    delete_after_seconds: int | None = None, operation_id: str | None = None,
    image_paths: list[str] | None = None, source_operation_id: str | None = None,
    source_artifact_id: str | None = None,
) -> list[TextContent]:
    """Generate with ordered image_paths or edit a saved image in a new background job.

    Pass source_operation_id and, if the operation has multiple images, source_artifact_id.
    The saved image is attached first. Query the returned operation handle.
    """
    return await _submit(prompt=prompt, media_type="image", model=model, thinking_level=thinking_level,
                         image_path=None, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_image", edit=False,
                         image_paths=image_paths, source_operation_id=source_operation_id,
                         source_artifact_id=source_artifact_id)


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


async def gemini_generate_video(
    prompt: str, model: str = "flash", thinking_level: str = "standard", image_path: str | None = None,
    output_dir: str | None = None, filename: str | None = None, timeout_seconds: int | None = None,
    retain_chat: bool = False, delete_after_seconds: int | None = None, operation_id: str | None = None,
    duration_seconds: int | None = None,
    aspect_ratio: Literal["16:9", "9:16"] | None = None,
) -> list[TextContent]:
    """Generate a video in the background using the Gemini App's Create video mode by default.

    Use model="gemini-omni-1.2-flash" to explicitly call the paid Gemini API via
    GEMINI_API_KEY. Web videos accept 10 or 15 seconds and 16:9 or 9:16.
    The explicit API route accepts duration_seconds from 3 to 15.
    Poll the original operation_id for a verified MP4.
    """
    return await _submit(prompt=prompt, media_type="video", model=model, thinking_level=thinking_level,
                         image_path=image_path, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_video", edit=False,
                         duration_seconds=duration_seconds, aspect_ratio=aspect_ratio)


async def gemini_generate_music(
    prompt: str, model: str = "flash", thinking_level: str = "standard", output_dir: str | None = None,
    filename: str | None = None, timeout_seconds: int | None = None, retain_chat: bool = False,
    delete_after_seconds: int | None = None, operation_id: str | None = None,
    music_duration: MusicDuration | None = None,
    music_vocals: MusicVocals | None = None,
) -> list[TextContent]:
    """Submit Web music in the background and query the returned operation handle.

    Choose music_duration (short, standard) and music_vocals (instrumental,
    vocals, auto) to set the corresponding Gemini Web options.
    """
    return await _submit(prompt=prompt, media_type="music", model=model, thinking_level=thinking_level,
                         image_path=None, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_music", edit=False,
                         music_duration=music_duration, music_vocals=music_vocals)


def build_server(*, full: bool = False) -> MCPServer:
    server = MCPServer(name="Gemini functions", version=__version__, lifespan=server_lifespan,
                       instructions=_INSTRUCTIONS, strict_arguments=not full)
    if full:
        from ..tools.chat import register_chat_tools

        register_assist_tools(selected_tools(server, set(FUNCTION_FULL_TOOLS)), notebook_context=True)
        register_chat_tools(selected_tools(server, {
            "gemini_start_chat", "gemini_send_message", "gemini_list_sessions", "gemini_reset_session",
        }))
    else:
        register_lean_assist_tools(server)
    register_operation_tools(server)
    register_task_generation_tools(server)
    if full:
        from ..tools.research import register_research_tools

        register_research_tools(selected_tools(server, {
            "gemini_list_research_report_actions", "gemini_create_from_research_report",
        }))
        for handler in (gemini_generate_image, gemini_edit_image, gemini_generate_video, gemini_generate_music):
            server.tool(annotations=MUTATES_REMOTE)(handler)
    else:
        server.tool(annotations=MUTATES_REMOTE)(gemini_create_media)
    return server


mcp = build_server()


def main(*, full: bool = False) -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Gemini functions (v%s)", __version__)
    init_startup_infrastructure()
    (build_server(full=True) if full else mcp).run()


if __name__ == "__main__":
    main()
