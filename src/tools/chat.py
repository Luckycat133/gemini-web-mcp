import uuid
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client, initialize_client, store_session, get_session, remove_session

logger = logging.getLogger(__name__)


def register_chat_tools(mcp: FastMCP) -> None:
    """Register all chat-related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_chat(
        message: str,
        model: str = "unspecified",
    ) -> list[TextContent]:
        """Send a single chat message to Gemini.

        Available models:
        - unspecified (default)
        - gemini-3.0-pro
        - gemini-3.0-flash
        - gemini-3.0-flash-thinking
        - gemini-2.5-pro

        Args:
            message: The message text to send
            model: Model to use (default: unspecified)

        Returns:
            Gemini's response as TextContent list
        """
        client = get_gemini_client()
        await initialize_client()

        logger.info(f"Sending message to Gemini (model: {model})")

        try:
            response = await client.generate_content(message, model=model)

            result_text = response.text

            # Add images if available
            if response.images:
                result_text += "\n\n📷 Images in response:\n"
                for i, img in enumerate(response.images, 1):
                    img_info = f"{i}. {img.title or 'Untitled image'}"
                    if hasattr(img, "url"):
                        img_info += f": {img.url}"
                    result_text += f"\n{img_info}"

            logger.info("Received response from Gemini")
            return [TextContent(type="text", text=result_text)]
        except Exception as e:
            logger.error(f"Error communicating with Gemini: {e}")
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]

    @mcp.tool()
    async def gemini_start_chat(
        model: str = "unspecified",
        gem: Optional[str] = None,
    ) -> list[TextContent]:
        """Create a new multi-turn chat session.

        Args:
            model: Model to use for the session
            gem: Optional gem id or name to use as system prompt

        Returns:
            Session ID for future interactions
        """
        client = get_gemini_client()
        await initialize_client()

        session_id = str(uuid.uuid4())
        logger.info(f"Creating new chat session (ID: {session_id}, model: {model})")

        chat_kwargs = {"model": model}
        if gem:
            chat_kwargs["gem"] = gem

        session = client.start_chat(**chat_kwargs)
        store_session(session_id, session)

        return [
            TextContent(
                type="text",
                text=f"✅ Chat session created successfully!\nSession ID: {session_id}\n\nUse `gemini_send_message` to continue the conversation."
            )
        ]

    @mcp.tool()
    async def gemini_send_message(
        session_id: str,
        message: str,
    ) -> list[TextContent]:
        """Send a message in an existing chat session.

        Args:
            session_id: Session ID from gemini_start_chat
            message: The message text to send

        Returns:
            Gemini's response as TextContent list
        """
        session = get_session(session_id)

        if not session:
            return [
                TextContent(
                    type="text",
                    text=f"❌ Error: Session not found. Please create a new session first."
                )
            ]

        logger.info(f"Sending message to chat session {session_id}")

        try:
            response = await session.send_message(message)

            result_text = response.text

            # Add images if available
            if response.images:
                result_text += "\n\n📷 Images in response:\n"
                for i, img in enumerate(response.images, 1):
                    img_info = f"{i}. {img.title or 'Untitled image'}"
                    if hasattr(img, "url"):
                        img_info += f": {img.url}"
                    result_text += f"\n{img_info}"

            logger.info(f"Received response for session {session_id}")
            return [TextContent(type="text", text=result_text)]
        except Exception as e:
            logger.error(f"Error in chat session {session_id}: {e}")
            return [TextContent(type="text", text=f"❌ Error: {str(e)}")]

    @mcp.tool()
    async def gemini_reset_session(session_id: str) -> list[TextContent]:
        """Reset and remove a chat session.

        Args:
            session_id: Session ID to reset

        Returns:
            Confirmation message
        """
        remove_session(session_id)
        return [
            TextContent(
                type="text",
                text=f"✅ Session {session_id} has been reset and removed."
            )
        ]

    @mcp.tool()
    async def gemini_list_sessions() -> list[TextContent]:
        """List active chat sessions.

        Returns:
            List of active session IDs
        """
        from ..auth import _sessions

        if not _sessions:
            return [TextContent(type="text", text="No active chat sessions.")]

        session_list = ["Active chat sessions:"]
        for i, (sid, _) in enumerate(_sessions.items(), 1):
            session_list.append(f"{i}. {sid}")

        return [TextContent(type="text", text="\n".join(session_list))]
