import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client

logger = logging.getLogger(__name__)


def register_image_tools(mcp: FastMCP) -> None:
    """Register all image-related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_generate_image(
        prompt: str,
        model: str = "imagen-3.0-generate-002",
    ) -> list[TextContent]:
        """Generate images with Gemini/Imagen.

        Args:
            prompt: Image generation prompt
            model: Model to use for image generation (default: imagen-3.0-generate-002)

        Returns:
            Generated image URLs or information
        """
        client = get_gemini_client()

        logger.info(f"Generating images with prompt: {prompt[:100]}...")

        try:
            response = await client.generate_images(prompt, model=model)

            if not response.images:
                return [
                    TextContent(
                        type="text",
                        text="No images were generated. You may need to use a different prompt or model."
                    )
                ]

            image_info = []
            for i, img in enumerate(response.images, 1):
                image_info.append(f"Image {i}: {img.url}")

            return [
                TextContent(
                    type="text",
                    text="Successfully generated images!\n\n" + "\n".join(image_info)
                )
            ]
        except Exception as e:
            logger.error(f"Error generating images: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]
