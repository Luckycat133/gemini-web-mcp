import os
import logging
from typing import Optional, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..client_wrapper import get_gemini_client, initialize_client
from ..constants import MODEL_CONFIG

logger = logging.getLogger(__name__)


def _validate_file_path(file_path: str) -> tuple[bool, str]:
    """验证文件路径安全性，防止路径遍历攻击。
    
    Args:
        file_path: 要验证的文件路径
        
    Returns:
        (is_safe, message): 是否安全及原因
    """
    if not file_path:
        return False, "文件路径不能为空"
    
    try:
        # 检查是否包含路径遍历序列
        if ".." in file_path:
            return False, "检测到路径遍历尝试，不允许使用 '..' 序列"
        
        normalized_path = os.path.normpath(file_path)
        
        if os.path.isabs(normalized_path):
            return True, normalized_path
        else:
            abs_path = os.path.abspath(normalized_path)
            return True, abs_path
    except Exception as e:
        return False, f"路径验证失败: {str(e)}"


def _validate_url(url: str) -> tuple[bool, str]:
    """验证 URL 格式是否正确。
    
    Args:
        url: 要验证的 URL
        
    Returns:
        (is_valid, message): 是否有效及原因
    """
    if not url:
        return False, "URL 不能为空"
    
    try:
        from urllib.parse import urlparse
        result = urlparse(url)
        if not result.scheme or not result.netloc:
            return False, "URL 格式无效"
        
        return True, url
    except Exception as e:
        return False, f"URL 验证失败: {str(e)}"


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
        is_safe, safe_path_or_error = _validate_file_path(file_path)
        if not is_safe:
            return [
                TextContent(
                    type="text",
                    text=f"❌ {safe_path_or_error}"
                )
            ]
        safe_file_path = safe_path_or_error

        if not os.path.exists(safe_file_path):
            return [
                TextContent(
                    type="text",
                    text=f"❌ 文件未找到: {safe_file_path}"
                )
            ]

        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        logger.info(f"上传文件: {safe_file_path}")

        try:
            prompt = analysis_prompt or "Please analyze this file and tell me what you see."

            response = await client.generate_content(
                prompt,
                files=[safe_file_path],
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
                    text=f"✅ Successfully analyzed {os.path.basename(safe_file_path)}\n\n{result_text}"
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
        is_valid, valid_url_or_error = _validate_url(url)
        if not is_valid:
            return [
                TextContent(
                    type="text",
                    text=f"❌ {valid_url_or_error}"
                )
            ]
        valid_url = valid_url_or_error

        client = get_gemini_client()
        await initialize_client()
        config = MODEL_CONFIG[model]

        prompt = analysis_prompt or f"Please analyze the content at this URL: {valid_url}"

        logger.info(f"分析 URL: {valid_url}")

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
