import logging
import asyncio
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .auth import get_gemini_client, reset_client
from .tools.chat import register_chat_tools
from .tools.image import register_image_tools
from .tools.file import register_file_tools
from .tools.research import register_research_tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Gemini Web MCP Server",
    instructions="A Model Context Protocol (MCP) server for Google Gemini Web API reverse engineering. "
    "Provides tools for chatting, image generation, file analysis, deep research, and more.",
)

# Register all tools
register_chat_tools(mcp)
register_image_tools(mcp)
register_file_tools(mcp)
register_research_tools(mcp)


@mcp.tool()
async def gemini_reset() -> list[TextContent]:
    """Reset the Gemini client and clear all sessions.

    Returns:
        Confirmation message
    """
    reset_client()
    return [
        TextContent(
            type="text",
            text="Gemini client has been reset and all sessions cleared."
        )
    ]


@mcp.tool()
async def gemini_health_check() -> list[TextContent]:
    """Check the health of the Gemini connection.

    Returns:
        Health status message
    """
    try:
        client = get_gemini_client()
        await client.generate_content("Hello")
        return [
            TextContent(
                type="text",
                text="✅ Gemini connection is healthy and working!"
            )
        ]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"❌ Gemini connection check failed: {str(e)}"
            )
        ]


def main() -> None:
    """Start the MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
