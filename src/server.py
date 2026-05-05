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
- gemini_research: In-depth research (Deep Research)
- gemini_generate_music: Generate music
- gemini_reset: Reset client
- gemini_health_check: Check connection
- gemini_list_models: List available models
- gemini_list_features: List all available features

Models available:
- unspecified (default)
- gemini-3.1-pro
- gemini-3-flash (2 versions available)
- gemini-3.0-flash-thinking
- gemini-3.0-pro
- gemini-2.5-pro

Image models: 2 available (ask to "generate image" in prompt)
Deep Research: Available for comprehensive research
Music generation: 2 models available (ask to "generate music")
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
    models_info = """🤖 Available Gemini Models (Current as of May 2026):

1. unspecified (default)
   - Uses Gemini's default model selection
   - Automatically picks best model for your task

2. gemini-3.1-pro
   - 🚀 Latest and most capable model
   - Best for complex reasoning, coding, and deep analysis
   - Advanced multi-modal capabilities

3. gemini-3-flash (2 versions available)
   - ⚡ Fast and efficient
   - Two variants: regular and thinking
   - Great for quick responses and everyday tasks

4. gemini-3.0-flash-thinking
   - 💭 Shows reasoning process
   - Includes thinking steps in response
   - Good for learning and understanding

5. gemini-3.0-pro
   - Previous generation advanced model
   - Still powerful and reliable

6. gemini-2.5-pro
   - Legacy pro model
   - Stable and compatible
"""
    return [TextContent(type="text", text=models_info)]


@mcp.tool()
async def gemini_list_features() -> list[TextContent]:
    """List all available Gemini features and capabilities.

    Returns:
        Complete feature list with descriptions
    """
    features_info = """✨ Gemini Web Features (Current as of May 2026):

📝 Text Generation & Conversation
- Multi-turn chat with memory
- Code generation and analysis
- Creative writing
- Reasoning and problem-solving

🖼️ Image Generation & Analysis (2 models)
- Generate images from text (Nano Banana)
- Edit and transform existing images
- Analyze and describe images
- Supports multiple image styles

🔍 Deep Research
- Comprehensive research on any topic
- Sources citations and references
- In-depth analysis and summaries
- Ask "do a deep research on..."

🎵 Music Generation (2 models)
- Generate original music from text prompt
- Different styles and genres
- Ask "generate music..."

📁 File Analysis
- Upload and analyze documents (PDF, TXT, etc.)
- Image understanding
- Code file review
- Data analysis

🌐 Web Integration
- YouTube video analysis
- Web page content analysis
- URL summarization
- Real-time information

🎨 Creative Tools
- Image generation and editing
- Art creation
- Design assistance

💡 Thinking Models
- See reasoning steps
- Learn how AI solves problems
- Transparent decision process

Note: Some features may have regional or account restrictions.
"""
    return [TextContent(type="text", text=features_info)]


@mcp.tool()
async def gemini_generate_music(
    prompt: str,
    model: str = "unspecified",
) -> list[TextContent]:
    """Generate music with Gemini using natural language prompts.

    Ask Gemini to "generate music" with descriptions of style, mood, genre, etc.
    Example: "Generate a relaxing piano track in jazz style"

    Args:
        prompt: Music generation prompt (include "generate music")
        model: Model to use (default: unspecified)

    Returns:
        Generated music information
    """
    client = get_gemini_client()
    await initialize_client()

    enhanced_prompt = f"{prompt}. Please generate music based on this description."

    logger.info(f"Generating music with prompt: {prompt[:100]}...")

    try:
        response = await client.generate_content(enhanced_prompt, model=model)

        result_text = response.text

        return [
            TextContent(
                type="text",
                text=f"🎵 Music Generation Results:\n\n{result_text}"
            )
        ]
    except Exception as e:
        logger.error(f"Error generating music: {e}")
        return [TextContent(type="text", text=f"❌ Error: {str(e)}")]


def main() -> None:
    """Start the MCP server."""
    logger.info("Starting Gemini Web MCP Server...")
    mcp.run()


if __name__ == "__main__":
    main()
