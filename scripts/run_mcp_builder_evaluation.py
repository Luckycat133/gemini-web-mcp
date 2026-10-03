"""Run the unchanged upstream mcp-builder evaluator with the project's SDK v2.

The connection adapter normalizes SDK ContentBlocks before the upstream engine
serializes them. The vendored Skill stays intact for whole-package upgrades.
Model-backed evaluation requires the optional upstream Anthropic dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.adapters.mcp_sdk import (  # noqa: E402
    Client,
    StdioServerParameters,
    create_mcp_http_client,
    sse_client,
    streamable_http_client,
)

_IMPORT_LOCK = threading.Lock()


class EvaluationConnection:
    """An SDK v2 connection implementing the upstream evaluator's small API."""

    def __init__(
        self, transport: str, *, command: str | None = None,
        args: list[str] | None = None, env: dict[str, str] | None = None,
        url: str | None = None, headers: dict[str, str] | None = None,
    ) -> None:
        self.transport = transport
        self.command, self.args, self.env = command, args or [], env
        self.url, self.headers = url, headers or {}
        self.client: Any = None
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> EvaluationConnection:
        stack = AsyncExitStack()
        self._stack = stack
        try:
            transport: Any
            if self.transport == "stdio":
                assert self.command is not None
                transport = StdioServerParameters(command=self.command, args=self.args, env=self.env)
            elif self.transport == "sse":
                assert self.url is not None
                transport = sse_client(self.url, headers=self.headers)
            else:
                assert self.url is not None
                http = await stack.enter_async_context(create_mcp_http_client(headers=self.headers))
                transport = streamable_http_client(self.url, http_client=http)
            self.client = await stack.enter_async_context(Client(transport, cache=None))
        except BaseException:
            await stack.aclose()
            self._stack = None
            raise
        return self

    async def __aexit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._stack is not None:
            try:
                await self._stack.__aexit__(exc_type, exc_value, traceback)
            finally:
                self._stack, self.client = None, None

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        seen: set[str] = set()
        while True:
            response = await self.client.list_tools(cursor=cursor)
            tools.extend({
                "name": tool.name, "description": tool.description, "input_schema": tool.input_schema,
            } for tool in response.tools)
            cursor = response.next_cursor
            if not cursor:
                return tools
            if cursor in seen or len(seen) >= 100:
                raise RuntimeError("MCP tool pagination did not complete within its bound.")
            seen.add(cursor)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.call_tool(name, arguments=arguments)
        return {
            "result_type": response.result_type,
            "is_error": response.is_error,
            "content": [
                block.model_dump(mode="json", by_alias=True, exclude_none=True)
                for block in response.content or ()
            ],
            "structured_content": response.structured_content,
        }


def create_connection(
    transport: str, command: str | None = None, args: list[str] | None = None,
    env: dict[str, str] | None = None, url: str | None = None,
    headers: dict[str, str] | None = None,
) -> EvaluationConnection:
    normalized = transport.lower().replace("-", "_")
    if normalized == "streamable_http":
        normalized = "http"
    if normalized not in {"stdio", "sse", "http"}:
        raise ValueError("Transport must be stdio, sse, or http.")
    if normalized == "stdio" and not command:
        raise ValueError("Command is required for stdio transport.")
    if normalized != "stdio" and not url:
        raise ValueError("URL is required for remote transport.")
    return EvaluationConnection(normalized, command=command, args=args, env=env, url=url, headers=headers)


def load_evaluator() -> ModuleType:
    """Bind the upstream engine to our connection adapter, without modifying it."""
    source = REPO_ROOT / ".agents/skills/mcp-builder/scripts/evaluation.py"
    spec = importlib.util.spec_from_file_location("gemini_mcp_builder_evaluation", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("The vendored mcp-builder evaluator is unavailable.")
    module = importlib.util.module_from_spec(spec)
    connections = ModuleType("connections")
    connections.create_connection = create_connection  # type: ignore[attr-defined]
    # Imports bind once when this standalone development command loads. Restore
    # the generic module name afterwards so unrelated imports stay untouched.
    with _IMPORT_LOCK:
        previous = sys.modules.get("connections")
        sys.modules["connections"] = connections
        try:
            spec.loader.exec_module(module)
        except ModuleNotFoundError as error:
            if error.name == "anthropic":
                raise RuntimeError(
                    "Model-backed evaluation requires anthropic; install the upstream evaluation dependency first.",
                ) from error
            raise
        finally:
            if previous is None:
                sys.modules.pop("connections", None)
            else:
                sys.modules["connections"] = previous
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eval_file", type=Path)
    parser.add_argument("-t", "--transport", choices=("stdio", "sse", "http"), default="stdio")
    parser.add_argument("-m", "--model", required=True, help="Explicit model ID for the evaluation provider")
    parser.add_argument("-c", "--command")
    parser.add_argument("-a", "--args", nargs="+")
    parser.add_argument("-e", "--env", nargs="+")
    parser.add_argument("-u", "--url")
    parser.add_argument("-H", "--header", dest="headers", nargs="+")
    parser.add_argument("-o", "--output", type=Path)
    return parser.parse_args()


async def run(arguments: argparse.Namespace) -> None:
    if not arguments.eval_file.is_file():
        raise ValueError("Evaluation XML file does not exist.")
    evaluator = load_evaluator()
    connection = create_connection(
        arguments.transport, command=arguments.command, args=arguments.args,
        env=evaluator.parse_env_vars(arguments.env) if arguments.env else None,
        url=arguments.url, headers=evaluator.parse_headers(arguments.headers) if arguments.headers else None,
    )
    async with connection:
        report = await evaluator.run_evaluation(arguments.eval_file, connection, arguments.model)
    if arguments.output:
        arguments.output.write_text(report, encoding="utf-8")
    else:
        print(report)


def main() -> None:
    arguments = parse_args()
    try:
        asyncio.run(run(arguments))
    except (ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
