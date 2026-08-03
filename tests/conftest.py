"""Pytest root conftest for the tests/ suite.

Compatibility shim for the mcp version resolved on the CI runner.

The project pins ``mcp>=2.0.0,<3``. On the CI runner the resolved mcp build
renamed ``mcp.shared.exceptions.McpError`` to ``MCPError``. fastmcp 3.x still
imports the old ``McpError`` name, so importing fastmcp fails with
``ImportError: cannot import name 'McpError'`` and every FastMCP-based manage
test errors at collection.

We bridge the rename here (runs before any test module is collected) so the
FastMCP-based tests stay collectable/runnable until fastmcp catches up with the
rename. This is a stopgap: once fastmcp imports ``MCPError`` directly the shim
becomes a no-op.
"""

import mcp.shared.exceptions as _mcp_exceptions

if not hasattr(_mcp_exceptions, "McpError") and hasattr(_mcp_exceptions, "MCPError"):
    _mcp_exceptions.McpError = _mcp_exceptions.MCPError
