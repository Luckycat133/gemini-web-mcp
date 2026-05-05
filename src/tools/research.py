import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client

logger = logging.getLogger(__name__)


def register_research_tools(mcp: FastMCP) -> None:
    """Register Deep Research related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_deep_research(
        query: str,
    ) -> list[TextContent]:
        """Perform a deep research on a topic using Gemini Deep Research.

        Args:
            query: The research query/topic

        Returns:
            Research report from Gemini
        """
        client = get_gemini_client()

        logger.info(f"Starting deep research on: {query}")

        try:
            research = await client.deep_research(query)

            logger.info("Deep research completed")

            return [
                TextContent(
                    type="text",
                    text=f"# Deep Research: {query}\n\n{research.report}"
                )
            ]
        except Exception as e:
            logger.error(f"Error in deep research: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]
