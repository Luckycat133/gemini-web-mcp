"""Project-owned boundary around the MCP Python SDK v2 surface.

Protocol models moved to the standalone ``mcp-types`` distribution in SDK v2.
Keeping those imports here makes the dependency boundary explicit and prevents
domain services from depending on server or wire-protocol implementation details.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from mcp.server import MCPServer as SDKMCPServer
from mcp_types import Icon, TextContent, ToolAnnotations


_CallableT = TypeVar("_CallableT", bound=Callable[..., Any])


class MCPServer(SDKMCPServer):
    """Keep content-block tools structured across supported MCP SDK v2 minors.

    MCP SDK 2.1 changed automatic schema detection so functions returning MCP
    content blocks are unstructured by default. This project intentionally
    publishes and validates schemas for those tools, so preserve that contract
    at the project-owned SDK boundary while still allowing explicit opt-out.
    """

    def tool(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        icons: list[Icon] | None = None,
        meta: dict[str, Any] | None = None,
        structured_output: bool | None = True,
    ) -> Callable[[_CallableT], _CallableT]:
        return super().tool(
            name=name,
            title=title,
            description=description,
            annotations=annotations,
            icons=icons,
            meta=meta,
            structured_output=structured_output,
        )


__all__ = [
    "MCPServer",
    "TextContent",
    "ToolAnnotations",
]
