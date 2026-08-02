"""Compatibility adapters for the currently supported MCP SDK surface."""

from .mcp_results import (
    attach_domain_result,
    domain_error_boundary,
    domain_text,
    exception_text,
)

__all__ = [
    "attach_domain_result",
    "domain_error_boundary",
    "domain_text",
    "exception_text",
]
