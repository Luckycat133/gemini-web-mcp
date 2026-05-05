import os
import logging
from typing import Optional, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def register_file_tools(mcp: FastMCP) -> None:
    """Register all file and URL related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_upload_file(
        file_path: str,
        analysis_prompt: Optional[str] = None,
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """上传文件供 Gemini 分析。

        支持: 图片、PDF、文档等。

        Args:
            file_path: 文件路径
            analysis_prompt: 可选分析提示词
            model: 模型选择 (fast/thinking/pro)
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        if not os.path.exists(file_path):
            return [
                TextContent(
                    type="text",
                    text=f"❌ 文件未找到: {file_path}"
                )
            ]

        logger.info(f"上传文件: {file_path}")

        try:
            prompt = analysis_prompt or "Please analyze this file and tell me what you see."

            response = await client.generate_content(
                prompt,
                files=[file_path],
                model=config["name"],
            )

            result_text = response.text

            if response.images:
                result_text += "\n\n📷 Images in response:\n"
                for i, img in enumerate(response.images, 1):
                    img_info = f"{i}. {img.title or 'Untitled image'}"
                    if hasattr(img, "url"):
                        img_info += f": {img.url}"
                    result_text += f"\n{img_info}"

            return [
                TextContent(
                    type="text",
                    text=f"✅ Successfully analyzed {os.path.basename(file_path)}\n\n{result_text}"
                )
            ]
        except Exception as e:
            logger.error(f"Error uploading/analyzing file: {e}")
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]

    @mcp.tool()
    async def gemini_analyze_url(
        url: str,
        analysis_prompt: Optional[str] = None,
        model: Literal["fast", "thinking", "pro"] = "fast",
    ) -> list[TextContent]:
        """分析 URL 内容。

        支持: YouTube 视频、网页等。

        Args:
            url: 网址
            analysis_prompt: 可选分析提示词
            model: 模型选择
        """
        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        prompt = analysis_prompt or f"Please analyze the content at this URL: {url}"

        logger.info(f"分析 URL: {url}")

        try:
            response = await client.generate_content(prompt, model=config["name"])

            result_text = response.text

            if response.images:
                result_text += "\n\n📷 Images in response:\n"
                for i, img in enumerate(response.images, 1):
                    img_info = f"{i}. {img.title or 'Untitled image'}"
                    if hasattr(img, "url"):
                        img_info += f": {img.url}"
                    result_text += f"\n{img_info}"

            return [TextContent(type="text", text=result_text)]
        except Exception as e:
            logger.error(f"Error analyzing URL: {e}")
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]
