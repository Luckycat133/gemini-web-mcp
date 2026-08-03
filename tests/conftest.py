"""Pytest root conftest for the tests/ suite.

The manage tests use ``tests._fastmcp_shim.FastMCP`` instead of the real
``fastmcp`` package: the project pins ``mcp>=2.0.0,<3``, but every published
fastmcp release was built against mcp 1.x and cannot be imported under mcp 2.x.
The shim exercises the real ``register_manage_tools`` registration and the real
tool handlers, so no compatibility shimming is required here.
"""
