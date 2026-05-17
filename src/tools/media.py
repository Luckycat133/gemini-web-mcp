"""
媒体生成 MCP 工具
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG
from .utils import parse_response

logger = logging.getLogger(__name__)


def register_media_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_generate_media(
        prompt: str,
        media_type: Literal["image", "video", "music"],
        model: Literal["fast", "thinking", "pro"] = "fast",
        image_path: Optional[str] = None,
    ) -> list[TextContent]:
        """媒体生成"""
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]
        prompts = {
            "image": f"Generate an image: {prompt}",
            "video": f"Generate a video: {prompt}",
            "music": f"Create a song: {prompt}",
        }
        logger.info(f"正在生成 {media_type}...")
        files = [image_path] if image_path else None
        response = await client.generate_content(prompt=prompts[media_type], files=files, model=config["name"])
        return parse_response(response, model)

    @mcp.tool()
    async def gemini_generate_music(
        prompt: str,
        model: Literal["fast", "thinking", "pro"] = "thinking",
    ) -> list[TextContent]:
        """音乐生成"""
        return await gemini_generate_media(prompt, "music", model)
