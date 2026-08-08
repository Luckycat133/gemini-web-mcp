"""Compatibility adapters for the currently supported MCP SDK surface."""

from .artifacts import append_artifact_block, format_artifact_block
from .mcp_sdk import MCPServer, TextContent, ToolAnnotations
from .mcp_results import (
    attach_domain_result,
    domain_error_boundary,
    domain_failure_text,
    domain_text,
    exception_text,
)

__all__ = [
    "append_artifact_block",
    "attach_domain_result",
    "domain_error_boundary",
    "domain_failure_text",
    "domain_text",
    "exception_text",
    "format_artifact_block",
    "MCPServer",
    "TextContent",
    "ToolAnnotations",
]
