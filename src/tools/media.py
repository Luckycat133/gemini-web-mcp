"""
媒体生成 MCP 工具
"""

import asyncio
import logging
import re
import shutil
import subprocess
import orjson
from mcp.server.fastmcp import FastMCP
from pathlib import Path
from mcp.types import TextContent
from typing import Literal, Optional

from gemini_webapi.constants import GRPC
from gemini_webapi.types import RPCData
from gemini_webapi.types.video import GeneratedMedia
from gemini_webapi.utils import extract_json_from_response, get_nested_value

from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_media_request
from .annotations import MUTATES_REMOTE
from .utils import extract_remote_chat_id, parse_response, validate_optional_image_path

logger = logging.getLogger(__name__)


def _safe_media_filename(prompt: str, media_type: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", prompt.strip())[:48].strip("._-")
    return stem or media_type


def _probe_duration(path: str) -> Optional[float]:
    if not shutil.which("ffprobe"):
        return None
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return float(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


async def _save_generated_media(
    response,
    *,
    media_type: str,
    output_dir: Optional[str],
    filename: Optional[str],
    prompt: str,
    media_items: Optional[list] = None,
) -> list[str]:
    media_items = media_items if media_items is not None else (getattr(response, "media", None) or [])
    if not media_items:
        return []

    destination = Path(output_dir or "generated_media").expanduser()
    saved_lines: list[str] = []
    for index, media in enumerate(media_items, 1):
        base_name = filename or _safe_media_filename(prompt, media_type)
        if len(media_items) > 1:
            base_name = f"{base_name}_{index}"

        save_kwargs = {"path": str(destination), "filename": base_name, "verbose": False}
        if media_type == "music":
            save_kwargs["download_type"] = "both"
        saved = await media.save(**save_kwargs)
        for kind, path in sorted((saved or {}).items()):
            if not path:
                continue
            line = f"{kind}: {path}"
            duration = _probe_duration(path)
            if duration is not None:
                line += f" ({duration:.2f}s)"
            saved_lines.append(line)
    return saved_lines


def _media_from_music_card(card_data, *, client, cid: str, rid: str, rcid: str) -> Optional[GeneratedMedia]:
    title = str(get_nested_value(card_data, [1, 2], "")) or "[Media]"
    is_mp4 = title.endswith(".mp4")
    media_url = str(get_nested_value(card_data, [1, 7, 1], ""))
    mp3_url = "" if is_mp4 else media_url
    mp4_url = media_url if is_mp4 else ""
    if not (mp3_url or mp4_url):
        return None
    return GeneratedMedia(
        url=mp4_url,
        thumbnail="",
        mp3_url=mp3_url,
        mp3_thumbnail="",
        title=title,
        cid=cid,
        rid=rid,
        rcid=rcid,
        client_ref=client,
        proxy=getattr(client, "proxy", None),
    )


async def _fetch_music_media_from_chat(client, cid: str) -> list[GeneratedMedia]:
    if not cid or not hasattr(client, "_batch_execute"):
        return []

    response = await client._batch_execute(
        [
            RPCData(
                rpcid=GRPC.READ_CHAT,
                payload=orjson.dumps([cid, 10, None, 1, [1], [4], None, 1]).decode("utf-8"),
            )
        ]
    )
    media_items: list[GeneratedMedia] = []
    for part in extract_json_from_response(response.text):
        part_body_str = get_nested_value(part, [2])
        if not part_body_str:
            continue
        part_body = orjson.loads(part_body_str)
        for conv_turn in get_nested_value(part_body, [0], []) or []:
            rid = get_nested_value(conv_turn, [0, 1], "")
            for candidate_data in get_nested_value(conv_turn, [3, 0], []) or []:
                rcid = get_nested_value(candidate_data, [0], "")
                music_cards = get_nested_value(candidate_data, [12, 0, "87"], []) or []
                for card_data in music_cards:
                    media = _media_from_music_card(
                        card_data,
                        client=client,
                        cid=cid,
                        rid=rid,
                        rcid=rcid,
                    )
                    if media:
                        media_items.append(media)
    return media_items


def _prepend_backend_note(parsed: list[TextContent], note_lines: list[str]) -> list[TextContent]:
    if not parsed or not note_lines:
        return parsed
    first = parsed[0]
    prefix = "\n".join(note_lines).strip()
    if not prefix:
        return parsed
    return [TextContent(type="text", text=f"{prefix}\n\n{first.text}".strip()), *parsed[1:]]


def _media_timeout(media_type: str, timeout_seconds: Optional[int]) -> int:
    if timeout_seconds and timeout_seconds > 0:
        return timeout_seconds
    if media_type == "image":
        return 180
    return 600


def _set_client_timeouts(client, timeout_seconds: int) -> tuple[Optional[float], Optional[float]]:
    previous_timeout = getattr(client, "timeout", None)
    previous_watchdog_timeout = getattr(client, "watchdog_timeout", None)
    if previous_timeout is not None:
        client.timeout = max(float(previous_timeout), float(timeout_seconds))
    if previous_watchdog_timeout is not None:
        client.watchdog_timeout = min(
            max(float(previous_watchdog_timeout), 120.0),
            max(float(timeout_seconds), 120.0),
        )
    return previous_timeout, previous_watchdog_timeout


def _restore_client_timeouts(
    client,
    previous_timeout: Optional[float],
    previous_watchdog_timeout: Optional[float],
) -> None:
    if previous_timeout is not None:
        client.timeout = previous_timeout
    if previous_watchdog_timeout is not None:
        client.watchdog_timeout = previous_watchdog_timeout


def register_media_tools(mcp: FastMCP):

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_generate_media(
        prompt: str,
        media_type: Literal["image", "video", "music"],
        model: str = "flash",
        thinking_level: str = "standard",
        image_path: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        output_dir: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> list[TextContent]:
        """媒体生成"""
        valid_image, safe_image_path, image_error = validate_optional_image_path(image_path)
        if not valid_image:
            return [TextContent(type="text", text=f"❌ {image_error}")]

        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        media_request = resolve_media_request(model, media_type, thinking_level)
        model_name = media_request["request_model"]
        effective_timeout = _media_timeout(media_type, timeout_seconds)
        prompts = {
            "image": f"Generate an image. Prompt: {prompt}",
            "video": (
                "Generate a video using Gemini's video generation capability. "
                f"Prompt: {prompt}"
            ),
            "music": (
                "Create music/audio using Gemini's music generation capability. "
                f"Prompt: {prompt}"
            ),
        }
        logger.info(
            "正在生成 %s，requested_model=%s effective_model=%s backend=%s",
            media_type,
            model,
            media_request["effective_alias"],
            media_request["backend_label"],
        )
        files = [safe_image_path] if safe_image_path else None
        previous_timeout, previous_watchdog_timeout = _set_client_timeouts(
            client,
            effective_timeout,
        )
        try:
            response = await asyncio.wait_for(
                client.generate_content(
                    prompt=prompts[media_type],
                    files=files,
                    model=model_name,
                    thinking_level=thinking_level,
                    timeout=effective_timeout,
                ),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(f"{media_type} generation timed out after {effective_timeout}s")
            return [
                TextContent(
                    type="text",
                    text=(
                        f"后端: {media_request['backend_label']}\n"
                        f"❌ {media_type} 生成超时: {effective_timeout}s 内没有收到完整结果。"
                        "视频/音乐通常需要更长时间或会被 Gemini Web 上游排队；"
                        "可增大 timeout_seconds 后重试。"
                    ),
                )
            ]
        except Exception as e:
            logger.error(f"{media_type} generation failed: {e}")
            return [
                TextContent(
                    type="text",
                    text=(
                        f"后端: {media_request['backend_label']}\n"
                        f"❌ {media_type} 生成失败: {str(e)}\n"
                        "说明: 当前封装通过 Gemini Web 的通用 generate_content 触发媒体能力，"
                        "视频/音乐可能被上游静默中止或长时间排队。"
                    ),
                )
            ]
        finally:
            _restore_client_timeouts(
                client,
                previous_timeout,
                previous_watchdog_timeout,
            )

        parsed = parse_response(response, media_request["effective_alias"])
        recovered_media = []
        if media_type == "music" and not (getattr(response, "media", None) or []):
            remote_chat_id = extract_remote_chat_id(response)
            try:
                recovered_media = await _fetch_music_media_from_chat(client, remote_chat_id or "")
            except Exception as e:
                logger.warning("无法从远端 chat 恢复音乐媒体 URL: %s", e)
        saved_lines = await _save_generated_media(
            response,
            media_type=media_type,
            output_dir=output_dir,
            filename=filename,
            prompt=prompt,
            media_items=recovered_media or None,
        )
        if saved_lines:
            parsed[0].text = f"{parsed[0].text}\n\nSaved files:\n" + "\n".join(saved_lines)
        schedule_remote_chat_cleanup_from_response(
            response,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=f"gemini_generate_media:{media_type}",
        )
        if not parsed[0].text.strip():
            return [
                TextContent(
                    type="text",
                    text=(
                        f"后端: {media_request['backend_label']}\n"
                        f"⚠️ {media_type} 请求已完成，但没有返回文本、图片、视频或音乐资源。"
                        "请换更明确的生成提示词，或稍后重试。"
                    ),
                )
            ]
        note_lines = [f"后端: {media_request['backend_label']}"]
        if media_request["note"]:
            note_lines.append(media_request["note"])
        if media_type == "image":
            note_lines.append("说明: Pro redo 属于网页生成后的二次操作，不是独立首轮生成模型。")
        return _prepend_backend_note(parsed, note_lines)

    @mcp.tool(annotations=MUTATES_REMOTE)
    async def gemini_generate_music(
        prompt: str,
        model: str = "flash",
        thinking_level: str = "extended",
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
        output_dir: Optional[str] = None,
        filename: Optional[str] = None,
    ) -> list[TextContent]:
        """音乐生成"""
        return await gemini_generate_media(
            prompt=prompt,
            media_type="music",
            model=model,
            thinking_level=thinking_level,
            timeout_seconds=timeout_seconds,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            output_dir=output_dir,
            filename=filename,
        )
