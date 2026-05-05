import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client, initialize_client

logger = logging.getLogger(__name__)


def register_research_tools(mcp: FastMCP) -> None:
    """Register research-related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_research(
        query: str,
        model: str = "unspecified",
    ) -> list[TextContent]:
        """Ask Gemini to perform in-depth research on a topic.

        Note: This uses Gemini's regular generation capabilities.
        Asking for "deep research" or "in-depth analysis" in your query
        will prompt Gemini to provide comprehensive information.

        Args:
            query: The research topic or question

        Returns:
            Research results from Gemini
        """
        client = get_gemini_client()
        await initialize_client()

        enhanced_query = f"Please perform in-depth research on: {query}. Provide comprehensive analysis with multiple sources if possible."

        logger.info(f"Starting research on: {query}")

        try:
            response = await client.generate_content(enhanced_query, model=model)

            result_text = response.text

            if response.images:
                result_text += "\n\n📷 Images in response:\n"
                for i, img in enumerate(response.images, 1):
                    img_info = f"{i}. {img.title or 'Untitled image'}"
                    if hasattr(img, "url"):
                        img_info += f": {img.url}"
                    result_text += f"\n{img_info}"

            logger.info("Research completed")

            return [
                TextContent(
                    type="text",
                    text=f"🔍 Research Results: {query}\n\n{result_text}"
                )
            ]
        except Exception as e:
            logger.error(f"Error in research: {e}")
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]
