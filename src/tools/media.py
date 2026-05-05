"""
媒体生成 MCP 工具
支持: 图像 (Nano Banana 2)、视频 (Veo 3.1)、音乐 (Lyria 3)
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def register_media_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_generate_media(
        prompt: str,
        media_type: Literal["image", "video", "music"],
        model: Literal["fast", "thinking", "pro"] = "fast",
        image_path: Optional[str] = None,
    ) -> list[TextContent]:
        """
        主动触发媒体生成。
        
        参数说明:
        - media_type: 
            * image: 图像生成 (所有模型使用 Nano Banana 2)
            * video: 视频生成 (Veo 3.1，最长60秒)
            * music: 音乐生成 (Flash=30秒片段, Thinking/Pro=完整歌曲)
        - model: 影响生成质量和音乐时长
        - image_path: 可选，图片转视频/音乐时提供
        
        提示词示例:
        - image: "a cute cat on a windowsill, watercolor style"
        - video: "a spaceship flying through the galaxy"
        - music: "a relaxing jazz piano song"
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        # 根据 media_type 构建专门的提示词
        media_prompts = {
            "image": f"Generate an image: {prompt}",
            "video": f"Generate a video: {prompt}",
            "music": f"Create a song: {prompt}",
        }
        
        full_prompt = media_prompts[media_type]
        
        # 构建输入内容
        contents = [full_prompt]
        
        # 添加图片输入（图片转视频/音乐）
        if image_path:
            try:
                from PIL import Image
                contents.append(Image.open(image_path))
            except Exception as e:
                logger.warning(f"无法加载参考图片: {e}")

        # 生成
        logger.info(f"正在生成 {media_type}...")
        response = await client.generate_content(contents, model=config["name"])

        outputs = []
        result_parts = [response.text] if response.text else []

        # 提取生成的媒体
        if media_type == "image":
            if hasattr(response, "images") and response.images:
                for i, img in enumerate(response.images, 1):
                    img_info = f"\n\n🖼️ 生成图片 {i}: {img.title or 'Untitled'}"
                    if hasattr(img, "url") and img.url:
                        img_info += f"\nURL: {img.url}"
                    if hasattr(img, "alt") and img.alt:
                        img_info += f"\n描述: {img.alt}"
                    result_parts.append(img_info)
            else:
                result_parts.append("\n\n⚠️ 未生成图片，请尝试调整提示词。")

        elif media_type == "video":
            if hasattr(response, "videos") and response.videos:
                for i, vid in enumerate(response.videos, 1):
                    vid_info = f"\n\n🎬 生成视频 {i}:"
                    if hasattr(vid, "title") and vid.title:
                        vid_info += f"\n标题: {vid.title}"
                    if hasattr(vid, "url") and vid.url:
                        vid_info += f"\nURL: {vid.url}"
                    if hasattr(vid, "duration"):
                        vid_info += f"\n时长: {vid.duration}秒"
                    result_parts.append(vid_info)
            else:
                result_parts.append("\n\n⚠️ 未生成视频，请确认区域限制和账户权限。")

        elif media_type == "music":
            music_type = "30秒片段" if model == "fast" else "完整歌曲 (~3分钟)"
            
            if hasattr(response, "audio_url") and response.audio_url:
                result_parts.append(f"\n\n🎵 音乐 ({music_type}): {response.audio_url}")
            
            if hasattr(response, "lyrics") and response.lyrics:
                result_parts.append(f"\n\n🎵 歌词:\n{response.lyrics}")
            
            if not hasattr(response, "audio_url") or not response.audio_url:
                result_parts.append("\n\n⚠️ 未生成音乐，请确认提示词。")

        outputs.append(TextContent(type="text", text="".join(result_parts)))
        return outputs

    @mcp.tool()
    async def gemini_generate_music(
        prompt: str,
        model: Literal["fast", "thinking", "pro"] = "thinking",
    ) -> list[TextContent]:
        """
        生成音乐（便捷工具）。
        
        - fast: Lyria 3 Clip (30秒片段)
        - thinking/pro: Lyria 3 Pro (完整歌曲)
        """
        return await gemini_generate_media(prompt, "music", model)
