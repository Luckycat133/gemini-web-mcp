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
            info = f"\n\n🎬 视频 {i}:"
            if hasattr(vid, "title") and vid.title:
                info += f"\n标题: {vid.title}"
            if hasattr(vid, "url") and vid.url:
                info += f"\nURL: {vid.url}"
            if hasattr(vid, "duration"):
                info += f"\n时长: {vid.duration}秒"
            result_parts.append(info)
    
    if hasattr(response, "audio_url") and response.audio_url:
        music_type = "30秒片段" if model == "fast" else "完整歌曲 (~3分钟)"
        result_parts.append(f"\n\n🎵 音乐 ({music_type}): {response.audio_url}")
    elif hasattr(response, "lyrics") and response.lyrics:
        result_parts.append(f"\n\n🎵 歌词:\n{response.lyrics}")
    
    return [TextContent(type="text", text="".join(result_parts))]
