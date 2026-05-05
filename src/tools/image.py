import logging

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client, initialize_client

logger = logging.getLogger(__name__)


def register_image_tools(mcp: FastMCP) -> None:
    """Register all image-related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_generate_image(
        prompt: str,
        model: str = "unspecified",
    ) -> list[TextContent]:
        """Generate images with Gemini using natural language prompts.

        Just ask Gemini to "generate" images in your prompt and it will use Nano Banana.
        Examples: "Generate an image of a cute cat", "Create a picture of a mountain landscape"

        Args:
            prompt: Image generation prompt (be sure to use words like "generate", "create", "make an image of")
            model: Model to use (default: unspecified)

        Returns:
            Generated image URLs and information
        """
        client = get_gemini_client()
        await initialize_client()

        logger.info(f"Generating images with prompt: {prompt[:100]}...")

        try:
            response = await client.generate_content(prompt, model=model)

            result_lines = ["✅ Gemini response:"]
            if response.text:
                result_lines.append(f"\n{response.text}")

            if not response.images:
                result_lines.append(
                    "\n⚠️ No images were generated. Try adding words like 'generate' or 'create an image of' to your prompt, or check your region/account restrictions."
                )
                return [TextContent(type="text", text="\n".join(result_lines))]

            result_lines.append("\n📷 Generated images:")
            for i, image in enumerate(response.images, 1):
                img_info = f"{i}. {image.title or 'Untitled image'}"
                if hasattr(image, "url"):
                    img_info += f"\n   URL: {image.url}"
                if hasattr(image, "alt") and image.alt:
                    img_info += f"\n   Description: {image.alt}"
                result_lines.append(img_info)

            return [TextContent(type="text", text="\n".join(result_lines))]
        except Exception as e:
            logger.error(f"Error generating images: {e}")
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]
