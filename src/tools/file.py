import os
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client

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
    ) -> list[TextContent]:
        """Upload a file for Gemini to analyze.

        Args:
            file_path: Path to the file to upload
            analysis_prompt: Optional prompt for analysis (default: analyze the file)

        Returns:
            Analysis results from Gemini
        """
        client = get_gemini_client()

        if not os.path.exists(file_path):
            return [
                TextContent(
                    type="text",
                    text=f"Error: File not found at path: {file_path}"
                )
            ]

        logger.info(f"Uploading file: {file_path}")

        try:
            uploaded_file = await client.upload_file(file_path)

            prompt = analysis_prompt or "Please analyze this file and tell me what you see."
            response = await client.generate_content([prompt, uploaded_file])

            return [
                TextContent(
                    type="text",
                    text=f"Successfully uploaded and analyzed {os.path.basename(file_path)}\n\n{response.text}"
                )
            ]
        except Exception as e:
            logger.error(f"Error uploading/analyzing file: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @mcp.tool()
    async def gemini_analyze_url(
        url: str,
        analysis_prompt: Optional[str] = None,
    ) -> list[TextContent]:
        """Ask Gemini to analyze a URL (webpage, YouTube, etc.).

        Args:
            url: The URL to analyze
            analysis_prompt: Optional prompt for analysis

        Returns:
            Analysis results from Gemini
        """
        client = get_gemini_client()

        prompt = analysis_prompt or f"Please analyze the content at this URL: {url}"

        logger.info(f"Analyzing URL: {url}")

        try:
            response = await client.generate_content(prompt)
            return [TextContent(type="text", text=response.text)]
        except Exception as e:
            logger.error(f"Error analyzing URL: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]
