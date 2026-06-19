"""
工具模块共享函数
"""

from typing import List, Optional
from mcp.types import TextContent


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
            music_type = {
                "flash-lite": "Lyria 3",
                "flash": "Lyria 3",
                "fast": "Lyria 3",
                "thinking": "Lyria 3",
                "pro": "Lyria 3 Pro",
            }.get(model, "时长取决于当前模型")
            info = f"\n\n🎵 音乐 {i} ({music_type}): {m.title or 'Untitled'}"
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
    """优先使用库提供的 text_delta，回退到完整 text。"""
    if hasattr(response, "text_delta"):
        return getattr(response, "text_delta", "") or ""
    return getattr(response, "text", "") or ""
