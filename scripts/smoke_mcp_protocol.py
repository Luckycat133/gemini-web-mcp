"""Perform real MCP initialize/list-tools handshakes against both stdio entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if __package__:
    from .smoke_profiles import COMPACT_TOOLS, PRIMARY_PROFILE_TOOLS
else:
    from smoke_profiles import COMPACT_TOOLS, PRIMARY_PROFILE_TOOLS  # type: ignore[import-not-found,no-redef]


def _resolve_executable(command: str) -> str:
    requested = Path(command)
    if requested.parent != Path("."):
        if not requested.is_file():
            raise RuntimeError(f"MCP smoke executable does not exist: {requested}")
        return str(requested.resolve())

    # Do not resolve the interpreter symlink: virtualenv console scripts live
    # beside the venv's Python shim, not beside the base interpreter target.
    beside_python = Path(sys.executable).parent / command
    if beside_python.is_file():
        return str(beside_python)
    resolved = shutil.which(command)
    if resolved is None:
        raise RuntimeError(f"Cannot locate MCP console entrypoint {command!r}")
    return resolved


def _safe_environment(profile: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment["GEMINI_AUTO_REFRESH"] = "false"
    environment["GEMINI_TOOLS"] = profile
    return environment


async def _handshake(
    command: str,
    *,
    profile: str,
    expected_tools: frozenset[str],
    cwd: Path,
) -> dict[str, object]:
    executable = _resolve_executable(command)
    parameters = StdioServerParameters(
        command=executable,
        env=_safe_environment(profile),
        cwd=cwd,
    )
    async with asyncio.timeout(30):
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()

    actual_tools = frozenset(tool.name for tool in listed.tools)
    missing = sorted(expected_tools - actual_tools)
    unexpected = sorted(actual_tools - expected_tools)
    if missing or unexpected:
        raise RuntimeError(
            f"{command} protocol tool contract drifted; missing={missing}, unexpected={unexpected}"
        )
    return {
        "command": command,
        "profile": profile,
        "protocol_version": initialized.protocolVersion,
        "server_name": initialized.serverInfo.name,
        "tools": len(actual_tools),
    }


async def _run(primary_command: str, compact_command: str, profile: str) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="gemini-protocol-smoke-") as directory:
        cwd = Path(directory)
        return [
            await _handshake(
                primary_command,
                profile=profile,
                expected_tools=PRIMARY_PROFILE_TOOLS[profile],
                cwd=cwd,
            ),
            await _handshake(
                compact_command,
                profile=profile,
                expected_tools=COMPACT_TOOLS,
                cwd=cwd,
            ),
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-command", default="gemini-mcp-server")
    parser.add_argument("--compact-command", default="gemini-mcp-skill-server")
    parser.add_argument("--profile", choices=sorted(PRIMARY_PROFILE_TOOLS), default="model")
    args = parser.parse_args()

    results = asyncio.run(_run(args.primary_command, args.compact_command, args.profile))
    print(json.dumps({"handshakes": results, "status": "ok"}, sort_keys=True))


if __name__ == "__main__":
    main()
