"""
媒体生成 MCP 工具
"""

import asyncio
import logging
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional

from ..client_wrapper import (
    cleanup_due_remote_chats,
    get_gemini_client,
    initialize_client,
    schedule_remote_chat_cleanup_from_response,
)
from ..constants import resolve_media_request
from .utils import parse_response

logger = logging.getLogger(__name__)


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

    @mcp.tool()
    async def gemini_generate_media(
        prompt: str,
        media_type: Literal["image", "video", "music"],
        model: str = "flash",
        thinking_level: str = "standard",
        image_path: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
    ) -> list[TextContent]:
        """媒体生成"""
        client = get_gemini_client()
        await initialize_client()
        await cleanup_due_remote_chats(client)
        media_request = resolve_media_request(model, media_type)
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
        files = [image_path] if image_path else None
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

    @mcp.tool()
    async def gemini_generate_music(
        prompt: str,
        model: str = "flash",
        thinking_level: str = "extended",
        timeout_seconds: Optional[int] = None,
        retain_chat: bool = False,
        delete_after_seconds: Optional[int] = None,
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
        )
