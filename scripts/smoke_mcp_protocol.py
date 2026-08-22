"""Perform real MCP initialize/list-tools handshakes against the primary, compact, and assist stdio entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters, stdio_client

if __package__:
    from .smoke_profiles import ASSIST_TOOLS, COMPACT_TOOLS, PRIMARY_PROFILE_TOOLS
else:
    from smoke_profiles import (  # type: ignore[import-not-found,no-redef]
        ASSIST_TOOLS,
        COMPACT_TOOLS,
        PRIMARY_PROFILE_TOOLS,
    )

ASSIST_SERVER_NAME = "gemini_assist_mcp"


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


def _require_exact_tool_contract(
    command: str,
    expected_tools: frozenset[str],
    actual_tools: frozenset[str],
) -> None:
    missing = sorted(expected_tools - actual_tools)
    unexpected = sorted(actual_tools - expected_tools)
    if missing or unexpected:
        raise RuntimeError(
            f"{command} protocol tool contract drifted; missing={missing}, unexpected={unexpected}"
        )


def _structured_domain_result(structured: object) -> dict[str, Any] | None:
    """Return the ``domain_result`` payload carried by one assist structured result."""
    if not isinstance(structured, dict):
        return None
    blocks = structured.get("result")
    if not (isinstance(blocks, list) and blocks and isinstance(blocks[0], dict)):
        return None
    meta = blocks[0].get("_meta")
    if not isinstance(meta, dict):
        return None
    domain_result = meta.get("domain_result")
    return domain_result if isinstance(domain_result, dict) else None


async def _handshake(
    command: str,
    *,
    profile: str,
    expected_tools: frozenset[str],
    cwd: Path,
    mode: str,
) -> dict[str, object]:
    executable = _resolve_executable(command)
    parameters = StdioServerParameters(
        command=executable,
        env=_safe_environment(profile),
        cwd=cwd,
    )
    async with asyncio.timeout(30):
        async with Client(stdio_client(parameters), mode=mode, cache=None) as client:
            listed = await client.list_tools()
            actual_tools = frozenset(tool.name for tool in listed.tools)
            if "gemini_get_tool_manifest" in actual_tools:
                representative_name = "gemini_get_tool_manifest"
                representative_arguments = {"response_format": "json"}
            elif "account" in actual_tools:
                representative_name = "account"
                representative_arguments = {"action": "manifest"}
            else:
                raise RuntimeError(f"{command} exposes no auth-free static representative tool")
            representative = await client.call_tool(representative_name, representative_arguments)
            protocol_version = client.protocol_version
            server_name = client.server_info.name if client.server_info is not None else None

    _require_exact_tool_contract(command, expected_tools, actual_tools)
    if representative.is_error or representative.result_type != "complete":
        raise RuntimeError(
            f"{command} representative call failed in {mode} mode: "
            f"is_error={representative.is_error}, result_type={representative.result_type}"
        )
    if not isinstance(representative.structured_content, dict):
        raise RuntimeError(f"{command} did not return MCP v2 structured content in {mode} mode")
    return {
        "command": command,
        "mode": mode,
        "profile": profile,
        "protocol_version": protocol_version,
        "server_name": server_name,
        "representative_tool": representative_name,
        "result_type": representative.result_type,
        "tools": len(actual_tools),
    }


async def _assist_handshake(
    command: str,
    *,
    profile: str,
    cwd: Path,
    mode: str,
) -> dict[str, object]:
    """Handshake the focused assist surface and its typed blank-query rejection.

    The assist surface has no auth-free static manifest tool, so its
    representative call is the blank-query ``gemini_search`` rejection: the
    typed INVALID_ARGUMENT domain failure must ride a normal non-error MCP
    result with the domain payload preserved in structured content.
    """
    executable = _resolve_executable(command)
    parameters = StdioServerParameters(
        command=executable,
        env=_safe_environment(profile),
        cwd=cwd,
    )
    async with asyncio.timeout(30):
        async with Client(stdio_client(parameters), mode=mode, cache=None) as client:
            listed = await client.list_tools()
            actual_tools = frozenset(tool.name for tool in listed.tools)
            representative = await client.call_tool("gemini_search", {"query": ""})
            protocol_version = client.protocol_version
            server_name = client.server_info.name if client.server_info is not None else None

    _require_exact_tool_contract(command, ASSIST_TOOLS, actual_tools)
    if server_name != ASSIST_SERVER_NAME:
        raise RuntimeError(
            f"{command} introduced itself as {server_name!r}, expected {ASSIST_SERVER_NAME!r}"
        )
    if representative.is_error or representative.result_type != "complete":
        raise RuntimeError(
            f"{command} blank-query search must stay a non-error result in {mode} mode: "
            f"is_error={representative.is_error}, result_type={representative.result_type}"
        )
    domain_result = _structured_domain_result(representative.structured_content)
    error = domain_result.get("error") if domain_result is not None else None
    error_code = error.get("code") if isinstance(error, dict) else None
    if domain_result is None or domain_result.get("ok") is not False or error_code != "INVALID_ARGUMENT":
        raise RuntimeError(
            f"{command} blank-query search did not return the typed INVALID_ARGUMENT domain "
            f"failure in {mode} mode: {domain_result!r}"
        )
    return {
        "command": command,
        "mode": mode,
        "profile": profile,
        "protocol_version": protocol_version,
        "server_name": server_name,
        "representative_tool": "gemini_search",
        "result_type": representative.result_type,
        "tools": len(actual_tools),
    }


async def _run(
    primary_command: str,
    compact_command: str,
    assist_command: str,
    profile: str,
) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="gemini-protocol-smoke-") as directory:
        cwd = Path(directory)
        results = []
        for mode in ("auto", "legacy"):
            results.extend(
                [
                    await _handshake(
                        primary_command,
                        profile=profile,
                        expected_tools=PRIMARY_PROFILE_TOOLS[profile],
                        cwd=cwd,
                        mode=mode,
                    ),
                    await _handshake(
                        compact_command,
                        profile=profile,
                        expected_tools=COMPACT_TOOLS,
                        cwd=cwd,
                        mode=mode,
                    ),
                    await _assist_handshake(
                        assist_command,
                        profile=profile,
                        cwd=cwd,
                        mode=mode,
                    ),
                ]
            )
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-command", default="gemini-mcp-server")
    parser.add_argument("--compact-command", default="gemini-mcp-skill-server")
    parser.add_argument("--assist-command", default="gemini-mcp-assist")
    parser.add_argument("--profile", choices=sorted(PRIMARY_PROFILE_TOOLS), default="model")
    args = parser.parse_args()

    results = asyncio.run(
        _run(args.primary_command, args.compact_command, args.assist_command, args.profile)
    )
    print(json.dumps({"handshakes": results, "status": "ok"}, sort_keys=True))


if __name__ == "__main__":
    main()
