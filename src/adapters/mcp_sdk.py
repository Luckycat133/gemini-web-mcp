"""Project-owned boundary around the MCP Python SDK v2 surface.

Protocol models moved to the standalone ``mcp-types`` distribution in SDK v2.
Keeping those imports here makes the dependency boundary explicit and prevents
domain services from depending on server or wire-protocol implementation details.
"""

from __future__ import annotations

from mcp.server import MCPServer
from mcp_types import TextContent, ToolAnnotations


__all__ = [
    "MCPServer",
    "TextContent",
    "ToolAnnotations",
]
