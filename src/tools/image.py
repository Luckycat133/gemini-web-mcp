"""
图像编辑 MCP 工具
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from typing import Literal, Optional
import logging

from ..client_wrapper import get_gemini_client, initialize_client, load_images
from ..constants import MODEL_CONFIG
from .utils import parse_response

logger = logging.getLogger(__name__)


def register_image_tools(mcp: FastMCP):

    @mcp.tool()
    async def gemini_edit_image(
        prompt: str,
        image_path: str,
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """图像编辑"""
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]
        contents = [f"Edit this image: {prompt}"]
        contents.extend(load_images([image_path]))
        logger.info("正在编辑图像...")
        response = await client.generate_content(contents, model=config["name"])
        return parse_response(response, model)

    @mcp.tool()
    async def gemini_variations(
        prompt: Optional[str] = None,
        image_path: Optional[str] = None,
        num_variations: int = 1,
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """图像变体"""
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]
        num_variations = min(max(1, num_variations), 4)
        base_prompt = "Generate image variations"
        if prompt:
            base_prompt += f" with style: {prompt}"
        contents = [base_prompt]
        if image_path:
            contents.extend(load_images([image_path]))
        logger.info(f"正在生成 {num_variations} 个图像变体...")
        response = await client.generate_content(contents, model=config["name"])
        return parse_response(response, model)
