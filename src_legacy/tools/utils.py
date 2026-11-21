"""
工具模块共享函数
"""

from typing import List, Optional
from ..adapters.mcp_sdk import TextContent
from ..services.inputs import (  # noqa: F401 - compatibility exports
    IMAGE_ATTACHMENT_EXTENSIONS, MAX_IMAGE_ATTACHMENT_BYTES,
    validate_local_file_path, validate_image_paths, validate_optional_image_path,
)

def extract_remote_chat_id(response) -> Optional[str]:
    cid = getattr(response, "cid", None)
    if isinstance(cid, str) and cid.startswith("c_"):
        return cid

    metadata = getattr(response, "metadata", None)
    if isinstance(metadata, list) and metadata:
        cid = metadata[0]
        if isinstance(cid, str) and cid.startswith("c_"):
            return cid
    return None


def parse_response(
    response,
    model: str = "flash",
    text_override: Optional[str] = None,
) -> List[TextContent]:
    """解析 Gemini 响应，提取文本、图片、视频、音乐"""
    result_parts = []
    text = text_override if text_override is not None else getattr(response, "text", "")
    if text:
        result_parts.append(text)

    if hasattr(response, "images") and response.images:
        for i, img in enumerate(response.images, 1):
            info = f"\n\n🖼️ 图片 {i}: {img.title or 'Untitled'}"
            if hasattr(img, "url") and img.url:
                info += f"\nURL: {img.url}"
            if hasattr(img, "alt") and img.alt:
                info += f"\n描述: {img.alt}"
            result_parts.append(info)

    if hasattr(response, "videos") and response.videos:
        for i, vid in enumerate(response.videos, 1):
            info = f"\n\n🎬 视频 {i}: {vid.title or 'Untitled'}"
            if hasattr(vid, "url") and vid.url:
                info += f"\nURL: {vid.url}"
            result_parts.append(info)

    if hasattr(response, "media") and response.media:
        for i, m in enumerate(response.media, 1):
            info = f"\n\n🎵 音乐 {i} (Lyria): {m.title or 'Untitled'}"
            if hasattr(m, "mp3_url") and m.mp3_url:
                info += f"\nMP3: {m.mp3_url}"
            if hasattr(m, "url") and m.url:
                info += f"\nURL: {m.url}"
            result_parts.append(info)

    remote_chat_id = extract_remote_chat_id(response)
    if remote_chat_id:
        result_parts.append(f"\n\nRemote chat ID: {remote_chat_id}")

    return [TextContent(type="text", text="".join(result_parts))]


def get_stream_text_piece(response) -> str:
    """Return one legacy text piece; collected streams use StreamTextAccumulator."""
    text_delta = getattr(response, "text_delta", None)
    if isinstance(text_delta, str) and text_delta:
        return text_delta
    return getattr(response, "text", "") or ""
