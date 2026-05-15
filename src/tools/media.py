"""
媒体生成 MCP 工具
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client, load_images
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
        contents = [prompts[media_type]]
        if image_path:
            contents.extend(load_images([image_path]))
        logger.info(f"正在生成 {media_type}...")
        response = await client.generate_content(contents, model=config["name"])
        return parse_response(response, model)

    @mcp.tool()
    async def gemini_generate_music(
        prompt: str,
        model: Literal["fast", "thinking", "pro"] = "thinking",
    ) -> list[TextContent]:
        """音乐生成"""
        return await gemini_generate_media(prompt, "music", model)
