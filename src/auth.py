import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_client: Optional["gemini_webapi.GeminiClient"] = None
_sessions: dict[str, "gemini_webapi.ChatSession"] = {}
_initialized: bool = False


def get_gemini_client() -> "gemini_webapi.GeminiClient":
    """Get or create the Gemini client instance.

    Returns:
        Initialized GeminiClient instance

    Raises:
        ValueError: If GEMINI_PSID environment variable not set
    """
    global _client, _initialized

    if _client is None:
        try:
            from gemini_webapi import GeminiClient
        except ImportError:
            raise ImportError("Please install gemini-webapi: pip install gemini-webapi")

        psid = os.environ.get("GEMINI_PSID")
        psidts = os.environ.get("GEMINI_PSIDTS", "")
        proxy = os.environ.get("GEMINI_PROXY")

        if not psid:
            raise ValueError(
                "Please set the GEMINI_PSID environment variable. "
                "Get this from gemini.google.com (F12 -> Application -> Cookies -> __Secure-1PSID"
            )

        logger.info("Initializing Gemini client...")
        _client = GeminiClient(psid, psidts, proxy=proxy)
        _initialized = False
        logger.info("Gemini client created successfully. Will initialize on first use")

    return _client


async def initialize_client():
    """Initialize the Gemini client (call init()).

    This must be called after get_gemini_client() once before any other operations.

    Returns:
        The initialized client
    """
    global _client, _initialized

    if _client is None:
        _client = get_gemini_client()

    if not _initialized:
        logger.info("Calling client.init()...")
        await _client.init(timeout=30, auto_close=False, auto_refresh=True)
        _initialized = True
        logger.info("Gemini client initialized and ready!")

    return _client


def store_session(session_id: str, session: "gemini_webapi.ChatSession") -> None:
    """Store a chat session.

    Args:
        session_id: Unique session identifier
        session: ChatSession object to store
    """
    _sessions[session_id] = session


def get_session(session_id: str) -> Optional["gemini_webapi.ChatSession"]:
    """Get a stored chat session.

    Args:
        session_id: Unique session identifier

    Returns:
        ChatSession object if found, None otherwise
    """
    return _sessions.get(session_id)


def remove_session(session_id: str) -> None:
    """Remove a stored chat session.

    Args:
        session_id: Unique session identifier
    """
    if session_id in _sessions:
        del _sessions[session_id]


def clear_sessions() -> None:
    """Clear all stored chat sessions."""
    _sessions.clear()


def reset_client() -> None:
    """Reset the Gemini client instance."""
    global _client, _initialized
    _client = None
    _initialized = False
    clear_sessions()
