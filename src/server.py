import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from .auth import get_gemini_client, initialize_client, reset_client
from .tools.chat import register_chat_tools
from .tools.image import register_image_tools
from .tools.file import register_file_tools
from .tools.research import register_research_tools

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "Gemini Web MCP Server",
    instructions="""A Model Context Protocol (MCP) server for Google Gemini using reverse-engineered web API.

Available tools:
- gemini_chat: Single chat message
- gemini_start_chat: Create multi-turn session
- gemini_send_message: Send message in session
- gemini_list_sessions: List active sessions
- gemini_reset_session: Reset a session
- gemini_generate_image: Generate images
- gemini_upload_file: Upload and analyze files
- gemini_analyze_url: Analyze URLs
- gemini_research: In-depth research
- gemini_reset: Reset client
- gemini_health_check: Check connection
- gemini_list_models: List available models

Models available:
- unspecified (default)
- gemini-3.0-pro
- gemini-3.0-flash
- gemini-3.0-flash-thinking
- gemini-2.5-pro
""",
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
            text="✅ Gemini client has been reset and all sessions cleared."
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
        await initialize_client()
        response = await client.generate_content("Hello! Just checking if you're working.")
        return [
            TextContent(
                type="text",
                text=f"✅ Gemini connection is healthy and working!\n\nResponse: {response.text[:100]}..."
            )
        ]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=f"❌ Gemini connection check failed: {str(e)}\n\nPlease check your GEMINI_PSID and GEMINI_PSIDTS environment variables."
            )
        ]


@mcp.tool()
async def gemini_list_models() -> list[TextContent]:
    """List all available Gemini models.

    Returns:
        List of models with descriptions
    """
    models_info = """🤖 Available Gemini Models:

1. unspecified (default)
   - Uses Gemini's default model selection

2. gemini-3.0-pro
   - Latest advanced model
   - Best for complex reasoning and tasks

3. gemini-3.0-flash
   - Fast and efficient
   - Good for quick responses

4. gemini-3.0-flash-thinking
   - Shows reasoning process
   - Includes thinking steps in response

5. gemini-2.5-pro
   - Previous generation pro model
   - Stable and reliable
"""
    return [TextContent(type="text", text=models_info)]


def main() -> None:
    """Start the MCP server."""
    logger.info("Starting Gemini Web MCP Server...")
    mcp.run()


if __name__ == "__main__":
    main()
