"""Public onboarding client for verified Gemini Web MCP installation paths.

The default preflight launches the installed stdio server and calls a static
text tool without reading Gemini credentials.  Live chat and image examples
are separately opt-in and keep requested/effective/observed backend evidence
distinct.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters, stdio_client
from mcp_types import CallToolResult


COOKIE_ENVIRONMENT_NAMES = ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC")


class OnboardingError(RuntimeError):
    """A public onboarding check could not prove its promised result."""


def _resolve_server_command(command: str | None = None) -> str:
    if command:
        candidate = Path(command)
        if candidate.parent != Path("."):
            if not candidate.is_file():
                raise OnboardingError(f"MCP server command does not exist: {candidate}")
            return str(candidate.resolve())
        resolved = shutil.which(command)
        if resolved:
            return resolved
        raise OnboardingError(f"Cannot locate MCP server command {command!r}")

    beside_python = Path(sys.executable).parent / "gemini-mcp-server"
    if beside_python.is_file():
        return str(beside_python)
    resolved = shutil.which("gemini-mcp-server")
    if resolved:
        return resolved
    raise OnboardingError("Cannot locate the installed gemini-mcp-server entrypoint")


def _server_environment(*, profile: str, allow_live_account: bool) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["GEMINI_TOOLS"] = profile
    if not allow_live_account:
        for name in COOKIE_ENVIRONMENT_NAMES:
            environment.pop(name, None)
        environment["GEMINI_AUTO_REFRESH"] = "false"
    return environment


async def _call_installed_tool(
    name: str,
    arguments: Mapping[str, Any],
    *,
    profile: str,
    allow_live_account: bool,
    server_command: str | None = None,
) -> tuple[CallToolResult, str | None, str | None]:
    executable = _resolve_server_command(server_command)
    environment = _server_environment(profile=profile, allow_live_account=allow_live_account)
    with tempfile.TemporaryDirectory(prefix="gemini-onboarding-") as directory:
        parameters = StdioServerParameters(
            command=executable,
            env=environment,
            cwd=Path(directory),
        )
        async with asyncio.timeout(45 if not allow_live_account else 720):
            async with Client(stdio_client(parameters), mode="auto", cache=None) as client:
                result = await client.call_tool(name, dict(arguments))
                version = client.server_info.version if client.server_info is not None else None
                return result, client.protocol_version, version


def _first_text(result: CallToolResult) -> str:
    for item in result.content:
        if getattr(item, "type", None) == "text":
            text = getattr(item, "text", None)
            if isinstance(text, str):
                return text
    raise OnboardingError("The MCP tool did not return text content")


def domain_result_from_call(result: CallToolResult) -> dict[str, Any]:
    """Extract the typed domain result from either decoded or wire content."""

    for item in result.content:
        meta = getattr(item, "meta", None)
        if isinstance(meta, Mapping) and isinstance(meta.get("domain_result"), Mapping):
            return dict(meta["domain_result"])

    structured = result.structured_content
    if isinstance(structured, Mapping):
        items = structured.get("result")
        if isinstance(items, Sequence) and not isinstance(items, (str, bytes)):
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                meta = item.get("_meta")
                if isinstance(meta, Mapping) and isinstance(meta.get("domain_result"), Mapping):
                    return dict(meta["domain_result"])
    raise OnboardingError("The MCP tool did not return _meta.domain_result")


def verify_local_image_artifacts(
    domain_result: Mapping[str, Any],
    *,
    output_dir: Path,
) -> list[dict[str, Any]]:
    """Verify local image artifacts independently from response prose."""

    if domain_result.get("ok") is not True:
        error = domain_result.get("error")
        code = error.get("code") if isinstance(error, Mapping) else "UNKNOWN"
        raise OnboardingError(f"Image generation did not succeed: {code}")

    data = domain_result.get("data")
    if not isinstance(data, Mapping):
        raise OnboardingError("Image generation returned no structured artifact data")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        raise OnboardingError("Image generation returned no artifact list")

    output_root = output_dir.expanduser().resolve()
    verified: list[dict[str, Any]] = []
    for candidate in artifacts:
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("kind") != "image" or candidate.get("state") != "local":
            continue
        local_path = candidate.get("local_path")
        if not isinstance(local_path, str) or not local_path:
            continue
        path = Path(local_path).expanduser().resolve()
        try:
            path.relative_to(output_root)
        except ValueError as exc:
            raise OnboardingError(f"Artifact escaped the requested output directory: {path}") from exc
        if not path.is_file():
            raise OnboardingError(f"Artifact file is missing: {path}")
        actual_size = path.stat().st_size
        if actual_size <= 0 or candidate.get("size_bytes") != actual_size:
            raise OnboardingError(f"Artifact size verification failed: {path}")
        mime_type = candidate.get("mime_type")
        if not isinstance(mime_type, str) or not mime_type.startswith("image/"):
            raise OnboardingError(f"Artifact MIME verification failed: {path}")
        width = candidate.get("width")
        height = candidate.get("height")
        if not isinstance(width, int) or width <= 0 or not isinstance(height, int) or height <= 0:
            raise OnboardingError(
                "Image dimensions were not verified; install the image or all extra and retry"
            )
        verification = candidate.get("verification")
        if not isinstance(verification, Mapping) or verification.get("status") != "verified":
            raise OnboardingError(f"Artifact verification status is not verified: {path}")
        verified.append(
            {
                "id": candidate.get("id"),
                "local_path": str(path),
                "mime_type": mime_type,
                "size_bytes": actual_size,
                "width": width,
                "height": height,
                "verification": "verified",
            }
        )

    if not verified:
        raise OnboardingError(
            "No verified local image artifact was returned; a remote URI or response text alone is not enough"
        )
    return verified


async def run_preflight(*, server_command: str | None = None) -> dict[str, Any]:
    result, protocol_version, server_version = await _call_installed_tool(
        "gemini_get_tool_manifest",
        {"response_format": "json"},
        profile="model",
        allow_live_account=False,
        server_command=server_command,
    )
    if result.is_error or result.result_type != "complete":
        raise OnboardingError("The offline text tool call did not complete successfully")
    try:
        manifest = json.loads(_first_text(result))
    except json.JSONDecodeError as exc:
        raise OnboardingError("The offline text tool returned invalid JSON") from exc
    if not isinstance(manifest, Mapping) or manifest.get("server") != "gemini_web_mcp":
        raise OnboardingError("The offline text tool returned an unexpected manifest")
    groups = manifest.get("current_tool_groups")
    if groups != ["model"]:
        raise OnboardingError(f"The onboarding profile drifted: {groups!r}")
    enabled_count = manifest.get("current_enabled_count")
    if not isinstance(enabled_count, int) or enabled_count <= 0:
        raise OnboardingError("The model profile exposed no tools")
    return {
        "status": "ok",
        "mode": "offline",
        "credentials_accessed": False,
        "text_tool": "gemini_get_tool_manifest",
        "profile": "model",
        "enabled_tools": enabled_count,
        "protocol_version": protocol_version,
        "server_version": server_version,
    }


async def run_chat(
    prompt: str,
    *,
    model: str,
    thinking_level: str,
    server_command: str | None = None,
) -> dict[str, Any]:
    result, protocol_version, server_version = await _call_installed_tool(
        "gemini_chat",
        {
            "message": prompt,
            "model": model,
            "thinking_level": thinking_level,
            "temporary": True,
        },
        profile="model",
        allow_live_account=True,
        server_command=server_command,
    )
    domain_result = domain_result_from_call(result)
    if result.is_error or domain_result.get("ok") is not True:
        error = domain_result.get("error")
        code = error.get("code") if isinstance(error, Mapping) else "UNKNOWN"
        raise OnboardingError(f"Live text call failed: {code}")
    return {
        "status": "ok",
        "mode": "live",
        "text_tool": "gemini_chat",
        "protocol_version": protocol_version,
        "server_version": server_version,
        "text": _first_text(result),
        "result": domain_result,
    }


async def run_image(
    prompt: str,
    *,
    output_dir: Path,
    model: str,
    filename: str | None,
    server_command: str | None = None,
) -> dict[str, Any]:
    output_root = output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    arguments: dict[str, Any] = {
        "prompt": prompt,
        "media_type": "image",
        "model": model,
        "output_dir": str(output_root),
    }
    if filename:
        arguments["filename"] = filename
    result, protocol_version, server_version = await _call_installed_tool(
        "gemini_generate_media",
        arguments,
        profile="core",
        allow_live_account=True,
        server_command=server_command,
    )
    domain_result = domain_result_from_call(result)
    verified = verify_local_image_artifacts(domain_result, output_dir=output_root)
    data = domain_result["data"]
    assert isinstance(data, Mapping)
    observed_backend = data.get("observed_backend")
    return {
        "status": "ok",
        "mode": "live",
        "media_tool": "gemini_generate_media",
        "protocol_version": protocol_version,
        "server_version": server_version,
        "routing": {
            "requested_model": data.get("requested_model"),
            "request_model": data.get("request_model"),
            "effective_backend": data.get("effective_backend"),
            "observed_backend": observed_backend,
            "observed_backend_status": "observed" if observed_backend else "not_reported",
        },
        "artifacts": verified,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server-command",
        help="Override the sibling gemini-mcp-server entrypoint (primarily for verification)",
    )
    subparsers = parser.add_subparsers(dest="action")
    subparsers.add_parser("preflight", help="Call a static text tool without Gemini credentials")

    chat = subparsers.add_parser("chat", help="Make an explicitly authorized live text call")
    chat.add_argument("--allow-live-account", action="store_true", required=True)
    chat.add_argument("--prompt", required=True)
    chat.add_argument("--model", default="flash")
    chat.add_argument("--thinking-level", choices=("standard", "extended"), default="standard")

    image = subparsers.add_parser("image", help="Generate and independently verify a local image artifact")
    image.add_argument("--allow-live-account", action="store_true", required=True)
    image.add_argument("--prompt", required=True)
    image.add_argument("--output-dir", type=Path, required=True)
    image.add_argument("--model", default="flash")
    image.add_argument("--filename")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    action = args.action or "preflight"
    try:
        if action == "preflight":
            payload = asyncio.run(run_preflight(server_command=args.server_command))
        elif action == "chat":
            payload = asyncio.run(
                run_chat(
                    args.prompt,
                    model=args.model,
                    thinking_level=args.thinking_level,
                    server_command=args.server_command,
                )
            )
        elif action == "image":
            payload = asyncio.run(
                run_image(
                    args.prompt,
                    output_dir=args.output_dir,
                    model=args.model,
                    filename=args.filename,
                    server_command=args.server_command,
                )
            )
        else:  # pragma: no cover - argparse constrains this branch.
            parser.error(f"Unsupported action: {action}")
    except (OnboardingError, TimeoutError) as exc:
        parser.exit(1, f"onboarding failed: {exc}\n")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
