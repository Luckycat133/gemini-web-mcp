"""Filter existing MCP tool registrations without changing their handlers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from .mcp_sdk import MCPServer


class _SelectedTools:
    def __init__(self, server: MCPServer, names: set[str]):
        self.server = server
        self.names = names

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Any], Any]:
        def decorate(function: Any) -> Any:
            name = kwargs.get("name") or (args[0] if args else None) or function.__name__
            if name in self.names:
                return self.server.tool(*args, **kwargs)(function)
            return function
        return decorate


def selected_tools(server: MCPServer, names: set[str]) -> MCPServer:
    """Use where a registration function exposes more tools than a surface needs."""
    return cast(MCPServer, _SelectedTools(server, names))
