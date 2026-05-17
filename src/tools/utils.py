"""
工具模块共享函数
"""

from typing import List, Optional
from mcp.types import TextContent


def parse_response(response, model: str = "fast") -> List[TextContent]:
    """解析 Gemini 响应，提取文本、图片、视频、音乐"""
    result_parts = []
    if response.text:
        result_parts.append(response.text)

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
            music_type = "30秒片段" if model == "fast" else "完整歌曲 (~3分钟)"
            info = f"\n\n🎵 音乐 {i} ({music_type}): {m.title or 'Untitled'}"
            if hasattr(m, "mp3_url") and m.mp3_url:
                info += f"\nMP3: {m.mp3_url}"
            if hasattr(m, "url") and m.url:
                info += f"\nURL: {m.url}"
            result_parts.append(info)

    return [TextContent(type="text", text="".join(result_parts))]
