"""Gemini Web MCP package metadata and dependency compatibility boundary."""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION_NAME = "gemini-mcp-server"


def _install_gemini_webapi_compatibility_aliases() -> None:
    """Normalize supported ``gemini-webapi`` private enum renames.

    ``gemini-webapi`` 2.1 renamed ``GRPC.READ_CHAT`` to
    ``GRPC.LIST_CONVERSATION_TURNS`` without changing the observed RPC.  Some
    compatibility recovery paths still consume the older attribute name.  Keep
    the alias at the dependency boundary so those paths support both tested
    upstream minor lines without copying a private RPC id into each caller.
    """

    try:
        from gemini_webapi.constants import GRPC
    except ImportError:
        return

    read_chat = getattr(GRPC, "READ_CHAT", None)
    list_turns = getattr(GRPC, "LIST_CONVERSATION_TURNS", None)
    if read_chat is None and list_turns is not None:
        setattr(GRPC, "READ_CHAT", list_turns)
    elif list_turns is None and read_chat is not None:
        setattr(GRPC, "LIST_CONVERSATION_TURNS", read_chat)


_install_gemini_webapi_compatibility_aliases()

try:
    __version__ = version(DISTRIBUTION_NAME)
except PackageNotFoundError:
    # A source tree can be imported before installation.  Do not duplicate the
    # release version here; installed distributions always use package metadata.
    __version__ = "0+unknown"


__all__ = ["DISTRIBUTION_NAME", "__version__"]
