"""Gemini functionality: conversation, understanding, research and creation."""

from __future__ import annotations

import logging
from typing import Any, Literal

from .. import __version__
from ..adapters import MCPServer, TextContent, domain_error_boundary, domain_failure_text, domain_text
from ..adapters.registration import selected_tools
from ..client_wrapper import init_startup_infrastructure, server_lifespan
from ..constants import OMNI_VIDEO_MODEL, normalize_model_alias
from ..domain import DomainErrorCode, DomainResult
from ..services.background import submit_media
from ..services.media_generation import (
    MusicDuration, MusicGenre, MusicVocals, music_option_arguments, music_options_error, video_options_error,
)
from ..services.operations import MAX_RESULT_WAIT_SECONDS
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
Notebook analysis, Gem prompts, interactive learning modes, and app requests.
The Flash compatibility default prefers Hydrogen; explicit Neon and Carbon use
exact discovered selectors and verified plan availability.
Use gemini_create_media for Gemini Web image, edit_image, video, and music
(including aspect_ratio for image/video and music_genre for music).
Use gemini_research for Deep Research and report_action derivatives on ready
research handles. Use gemini_run_task for explicit Task or ongoing browser work.
Use the companion CLI `gemini-web-cli` for Notebooks, Gems, Skills, Scheduled
Actions, Tasks, History, Preferences, Public Links, Quotas, and Cleanup.
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
    wait_seconds: int | None = 0,
    music_duration: MusicDuration | None = None, music_vocals: MusicVocals | None = None,
    music_genre: MusicGenre | None = None,
) -> list[TextContent]:
    if not prompt.strip():
        return _invalid_media("prompt must not be blank.")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        return _invalid_media("model must not be blank when provided.")
    if model is not None and normalize_model_alias(model) == OMNI_VIDEO_MODEL:
        return _invalid_media("Paid Gemini API (gemini-omni-1.2-flash / GEMINI_API_KEY) is disabled; use Gemini Web.")
    resolved_wait = (
        (120 if media_type == "image" else (240 if media_type == "music" else MAX_RESULT_WAIT_SECONDS))
        if wait_seconds is None
        else wait_seconds
    )
    if type(resolved_wait) is not int or not 0 <= resolved_wait <= MAX_RESULT_WAIT_SECONDS:
        return _invalid_media(f"wait_seconds must be a whole number from 0 through {MAX_RESULT_WAIT_SECONDS}.")
    if not _valid_media_filename(filename):
        return _invalid_media("filename must be a single nonempty file name.")
    invalid = image_workflow_options_error(
        media_type=media_type, image_path=image_path, image_paths=image_paths,
        source_operation_id=source_operation_id, source_artifact_id=source_artifact_id,
    )
    if invalid:
        return _invalid_media(invalid)
    invalid = video_options_error(
        media_type=media_type, model=model, thinking_level=thinking_level,
        duration_seconds=duration_seconds, delete_after_seconds=delete_after_seconds,
        background=True, aspect_ratio=aspect_ratio, image_path=image_path,
    )
    if invalid:
        return _invalid_media(invalid)
    invalid = music_options_error(
        media_type=media_type, music_duration=music_duration, music_vocals=music_vocals, music_genre=music_genre,
    )
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
        **music_option_arguments(music_duration, music_vocals, music_genre),
    }, operation=operation, operation_id=operation_id)
    handle = result.data.operation_id if result.data else None
    if result.ok and handle and resolved_wait > 0:
        from ..tools.operations import _present, _service

        service = _service()
        store = getattr(service, "store", None)
        if store is not None and store.get(handle) is not None:
            waited = await service.get_result(handle, wait_seconds=resolved_wait)
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
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] | None = None,
    image_paths: list[str] | None = None, source_operation_id: str | None = None,
    source_artifact_id: str | None = None,
    wait_seconds: int | None = None,
    music_duration: MusicDuration | None = None,
    music_vocals: MusicVocals | None = None,
    music_genre: MusicGenre | None = None,
) -> list[TextContent]:
    """Generate image, image edit, video, or music via Gemini Web.

    Images and edits support image_paths, source_operation_id, source_artifact_id,
    and aspect_ratio (16:9, 9:16, 1:1, 4:3, 3:4). Web videos accept duration_seconds
    10 or 15 (10s only when image_path reference is attached) and aspect_ratio 16:9
    or 9:16. Web music accepts music_duration (short, standard), music_vocals
    (instrumental, vocals, auto), and music_genre (auto, pop, hip_hop, rock, kpop,
    latin, electronic, rnb, country, afrobeats, reggae, jazz_blues, classical, folk,
    lofi, acoustic, cinematic, ambient). wait_seconds defaults to 120s for images,
    240s for music, and 300s for video (max 300s); pass 0 to return the operation
    handle immediately without waiting.
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
        music_duration=music_duration, music_vocals=music_vocals, music_genre=music_genre,
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
    """Generate a video in the background using the Gemini App's Create video mode.

    Web videos accept 10 or 15 seconds (10s with reference image) and 16:9 or 9:16.
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
    music_genre: MusicGenre | None = None,
) -> list[TextContent]:
    """Submit Web music in the background and query the returned operation handle.

    Choose music_duration (short, standard), music_vocals (instrumental,
    vocals, auto), and music_genre to set the corresponding Gemini Web options.
    """
    return await _submit(prompt=prompt, media_type="music", model=model, thinking_level=thinking_level,
                         image_path=None, output_dir=output_dir, filename=filename, timeout_seconds=timeout_seconds,
                         retain_chat=retain_chat, delete_after_seconds=delete_after_seconds,
                         operation_id=operation_id, operation="gemini_generate_music", edit=False,
                         music_duration=music_duration, music_vocals=music_vocals, music_genre=music_genre)


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
