"""Minimal FastMCP-compatible stand-in for the gemini-web-mcp manage tests.

The project pins ``mcp>=2.0.0,<3``. The FastMCP class is provided by the
standalone ``fastmcp`` package, but every published fastmcp release (2.14.x and
3.4.x) was built against mcp 1.x and imports symbols/modules that mcp 2.0.0 no
longer exposes (``mcp.server.fastmcp``, ``mcp.types.SDK*`` aliases, ``RequestT``,
``McpError``, ``AnyFunction``, ``request_ctx``, ...). As a result ``import
fastmcp`` fails outright under the project's pinned mcp, so these tests cannot
use the real FastMCP.

Until fastmcp ships mcp 2.x support, this tiny in-process double exercises the
*real* ``register_manage_tools`` registration and the *real* tool handler
functions -- which is exactly what the tests assert on. It intentionally does
not reimplement FastMCP's pydantic input validation; the test suite covers the
handler-side validation branches directly via ``_call_raw`` (which bypasses
``call_tool``).
"""

from typing import Any, Callable


class _RegisteredTool:
    """Mirror of the bits of FastMCP's tool handle the tests touch."""

    def __init__(self, name: str, fn: Callable[..., Any]) -> None:
        self.name = name
        self.fn = fn


class _ToolManager:
    def __init__(self, tools: dict[str, Callable[..., Any]]) -> None:
        self._tools = tools

    def get_tool(self, name: str) -> _RegisteredTool:
        return _RegisteredTool(name, self._tools[name])


class FastMCP:
    """Tiny FastMCP stand-in: just enough to register and dispatch tools."""

    def __init__(self, name: str = "test") -> None:
        self.name = name
        self._tools: dict[str, Callable[..., Any]] = {}

    def tool(self, annotations: Any = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._tools[func.__name__] = func
            return func

        return decorator

    @property
    def _tool_manager(self) -> _ToolManager:
        return _ToolManager(self._tools)

    async def call_tool(self, name: str, kwargs: dict[str, Any]) -> tuple[Any, Any]:
        func = self._tools[name]
        result = await func(**kwargs)
        return result, None
