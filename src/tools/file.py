import os
import logging
from typing import Optional, List

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client, initialize_client

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
        model: str = "unspecified",
    ) -> list[TextContent]:
        """Upload files for Gemini to analyze.

        Supported file types: images, PDFs, documents, etc.

        Args:
            file_path: Path to the file to upload
            analysis_prompt: Optional prompt for analysis (default: analyze the file)
            model: Model to use (default: unspecified)

        Returns:
            Analysis results from Gemini
        """
        client = get_gemini_client()
        await initialize_client()

        if not os.path.exists(file_path):
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error: File not found at path: {file_path}"
                )
            ]

        logger.info(f"Uploading file: {file_path}")

        try:
            prompt = analysis_prompt or "Please analyze this file and tell me what you see."

            response = await client.generate_content(
                prompt,
                files=[file_path],
                model=model,
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
        model: str = "unspecified",
    ) -> list[TextContent]:
        """Ask Gemini to analyze content from a URL.

        You can ask for YouTube, web pages, etc. just by mentioning the URL in your prompt.

        Args:
            url: The URL to analyze
            analysis_prompt: Optional prompt for analysis (default: analyze the URL)
            model: Model to use (default: unspecified)

        Returns:
            Analysis results from Gemini
        """
        client = get_gemini_client()
        await initialize_client()

        prompt = analysis_prompt or f"Please analyze the content at this URL: {url}"

        logger.info(f"Analyzing URL: {url}")

        try:
            response = await client.generate_content(prompt, model=model)

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
