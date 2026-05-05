import uuid
import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

from ..auth import get_gemini_client, store_session, get_session, remove_session

logger = logging.getLogger(__name__)


def register_chat_tools(mcp: FastMCP) -> None:
    """Register all chat-related MCP tools.

    Args:
        mcp: FastMCP server instance
    """

    @mcp.tool()
    async def gemini_chat(
        message: str,
        model: str = "gemini-2.5-pro",
        temperature: Optional[float] = None,
        image: Optional[str] = None,
    ) -> list[TextContent]:
        """Send a single chat message to Gemini.

        Args:
            message: The message text to send
            model: Model to use (default: gemini-2.5-pro)
            temperature: Generation temperature (optional)
            image: Optional image path or base64 data (optional)

        Returns:
            Gemini's response as TextContent list
        """
        client = get_gemini_client()

        logger.info(f"Sending message to Gemini (model: {model})")

        try:
            response = await client.generate_content(
                message,
                model=model,
                temperature=temperature,
            )

            text = response.text
            logger.info("Received response from Gemini")

            return [TextContent(type="text", text=text)]
        except Exception as e:
            logger.error(f"Error communicating with Gemini: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

    @mcp.tool()
    async def gemini_start_chat(
        system_instruction: str = "",
        model: str = "gemini-2.5-pro",
    ) -> list[TextContent]:
        """Create a new multi-turn chat session.

        Args:
            system_instruction: System instruction for the conversation
            model: Model to use for the session

        Returns:
            Session ID for future interactions
        """
        client = get_gemini_client()

        session_id = str(uuid.uuid4())
        logger.info(f"Creating new chat session (ID: {session_id}, model: {model})")

        session = client.start_chat(
            model=model,
            system_instruction=system_instruction,
        )

        store_session(session_id, session)

        return [
            TextContent(
                type="text",
                text=f"Chat session created successfully! Session ID: {session_id}"
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
                    text=f"Error: Session not found. Please create a new session first."
                )
            ]

        logger.info(f"Sending message to chat session {session_id}")

        try:
            response = await session.send_message(message)
            logger.info(f"Received response for session {session_id}")

            return [TextContent(type="text", text=response.text)]
        except Exception as e:
            logger.error(f"Error in chat session {session_id}: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]

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
                text=f"Session {session_id} has been reset and removed."
            )
        ]

    @mcp.tool()
    async def gemini_list_chats() -> list[TextContent]:
        """List recent chat history from Gemini Web.

        Returns:
            List of recent chats
        """
        client = get_gemini_client()

        try:
            chats = await client.list_chats(limit=20)
            chat_list = []

            for i, chat in enumerate(chats, 1):
                chat_list.append(f"{i}. {chat.title} (ID: {chat.id})")

            if not chat_list:
                return [TextContent(type="text", text="No chat history found.")]

            return [TextContent(type="text", text="\n".join(chat_list))]
        except Exception as e:
            logger.error(f"Error listing chats: {e}")
            return [TextContent(type="text", text=f"Error: {str(e)}")]
