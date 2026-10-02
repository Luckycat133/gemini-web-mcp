"""Public onboarding client for verified Gemini Web MCP installation paths.

The default preflight launches the installed stdio server and calls a static
text tool without reading Gemini credentials.  Live text and media examples
are separately opt-in and keep requested/effective/observed backend evidence
distinct.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from mcp import Client, MCPError, StdioServerParameters, stdio_client
from mcp_types import CallToolResult

from .domain import DomainErrorCode, OperationState


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
        beside_python = Path(sys.executable).parent / command
        if beside_python.is_file():
            return str(beside_python)
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
        if not allow_live_account:
            environment["GEMINI_STATE_DB_PATH"] = str(Path(directory) / "state.sqlite3")
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
        if isinstance(structured.get("ok"), bool) and isinstance(structured.get("meta"), Mapping):
            return dict(structured)
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


def verify_local_media_artifacts(payload: Mapping[str, Any], *, media_type: str) -> list[dict[str, Any]]:
    """Read and verify bytes independently of the MCP artifact's claimed status."""
    from .domain import ArtifactKind
    from .services.artifacts import artifact_from_local_path

    data = payload.get("data")
    candidates = data.get("artifacts", []) if isinstance(data, Mapping) else []
    allowed = {"audio", "video"} if media_type == "music" else {"video"}
    verified = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or candidate.get("kind") not in allowed or not candidate.get("local_path"):
            continue
        path = Path(candidate["local_path"]).expanduser().resolve()
        observed = artifact_from_local_path(ArtifactKind(candidate["kind"]), str(path))
        if observed.verification.status.value != "verified":
            raise OnboardingError("A returned local media artifact could not be verified")
        claimed = candidate.get("verification", {})
        if not isinstance(claimed, Mapping) or claimed.get("status") != "verified":
            raise OnboardingError("The server has not verified a returned media artifact")
        verified.append({
            "kind": observed.kind.value, "local_path": str(path), "mime_type": observed.mime_type,
            "size_bytes": observed.size_bytes, "duration_seconds": observed.duration_seconds,
            "width": observed.width, "height": observed.height,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "verification": "verified",
        })
    return verified


async def run_media_operation(
    prompt: str | None = None, *, media_type: str = "music", output_dir: Path | None = None,
    model: str = "flash", thinking_level: str = "standard", timeout_seconds: int = 720,
    idempotency_key: str | None = None, operation_id: str | None = None,
    server_command: str | None = None,
) -> dict[str, Any]:
    """Start once and retain a safe recovery receipt even if responses are lost."""
    if operation_id is None and (media_type not in {"music", "video"} or not prompt or not prompt.strip()):
        raise OnboardingError("Music/video starts require a nonblank prompt")
    executable = _resolve_server_command(server_command or "gemini-mcp-create")
    environment = _server_environment(profile="core", allow_live_account=True)
    parameters = StdioServerParameters(command=executable, env=environment)
    handle = operation_id
    key = idempotency_key if handle is not None else idempotency_key or uuid.uuid4().hex
    state = "unknown"
    last_observed_state = None
    artifacts: list[dict[str, Any]] = []
    error_code = None
    failure_stage = None
    phase = "initialize"
    start_attempted = completion_verified = False
    version = protocol = None
    try:
        async with asyncio.timeout(timeout_seconds):
            async with Client(stdio_client(parameters), mode="auto", cache=None) as client:
                version = client.server_info.version if client.server_info else None
                protocol = client.protocol_version
                if handle is None:
                    arguments = {"prompt": prompt, "model": model, "thinking_level": thinking_level,
                                 "idempotency_key": key, "output_dir": str((output_dir or Path("generated_media")).expanduser().resolve())}
                    phase = "start"
                    start_attempted = True
                    start_response = await client.call_tool(f"gemini_generate_{media_type}", arguments)
                    phase = "start_response"
                    started = domain_result_from_call(start_response)
                    data = started.get("data")
                    handle = data.get("operation_id") if isinstance(data, Mapping) else None
                    error_code = _media_result_error_code(started)
                    if not isinstance(handle, str) or not handle.strip():
                        handle = None
                        state = "failed" if started.get("ok") is False else "unknown"
                        raise OnboardingError("Media start returned no recovery handle")
                    state = _media_result_state(started)
                    last_observed_state = state
                while True:
                    phase = "result"
                    result_response = await client.call_tool("gemini_get_operation_result", {"operation_id": handle})
                    phase = "result_response"
                    payload = domain_result_from_call(result_response)
                    data = payload.get("data")
                    state = _media_result_state(payload)
                    last_observed_state = state
                    error_code = _media_result_error_code(payload)
                    if isinstance(data, Mapping):
                        operation = data.get("operation", media_type)
                        if not isinstance(operation, str) or operation not in {"music", "video"}:
                            raise OnboardingError("This handle does not identify a music/video operation")
                        media_type = str(operation)
                    phase = "verification"
                    artifacts = await asyncio.to_thread(verify_local_media_artifacts, payload, media_type=media_type)
                    if state == "completed":
                        if not payload.get("ok") or not artifacts:
                            raise OnboardingError("A completed media operation returned no verified local artifacts")
                        completion_verified = True
                        break
                    if state in {"cancelled", "expired", "unavailable"} or error_code in {"AUTH_REQUIRED", "AUTH_EXPIRED", "OPERATION_NOT_FOUND", "OPERATION_EXPIRED"}:
                        break
                    # Failed/timed-out operations with a source can still recover.
                    if state in {"failed", "timed_out"} and not (isinstance(data, Mapping) and data.get("continuation_possible")):
                        break
                    phase = "poll_wait"
                    await asyncio.sleep(2)
                phase = "shutdown"
    except TimeoutError:
        failure_stage = phase
        if not completion_verified:
            state = "timed_out"
            error_code = "TIMED_OUT"
    except Exception as error:
        # The start may already have reached the server. Never lose its key or
        # observed handle, and never leak the exception's response/message/data.
        failure_stage = phase
        if phase == "verification":
            error_code = "VERIFICATION_FAILED"
            state = "failed"
            artifacts = []
        else:
            if not (phase == "start_response" and state == "failed" and error_code):
                error_code = _media_receipt_error_code(error)
            if not completion_verified and state not in {"failed", "cancelled", "expired", "unavailable"}:
                state = "partial" if handle is not None else "unknown"
    if state == "completed":
        next_step = None
    elif handle is None:
        next_step = "Repeat the same start in the same authentication context with the returned idempotency_key to retrieve its handle; do not use a new key."
    else:
        next_step = "gemini-mcp-onboarding operation --allow-live-account --operation-id <operation_id>"
    status = "ok" if state == "completed" else "failed" if state in {"failed", "cancelled", "expired", "unavailable"} else "pending"
    return {"status": status, "mode": "live", "media_type": media_type,
            "operation_id": handle, "idempotency_key": key,
            "state": state, "error_code": error_code, "artifacts": artifacts,
            "last_observed_state": last_observed_state, "failure_stage": failure_stage,
            "start_attempted": start_attempted, "completion_verified": completion_verified,
            "next_step": next_step,
            "protocol_version": protocol, "server_version": version, "generation_resubmitted": False}


def _media_result_error_code(payload: Mapping[str, Any]) -> str | None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    return code if isinstance(code, str) and code in {item.value for item in DomainErrorCode} else "UPSTREAM_CHANGED"


def _media_result_state(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    meta = payload.get("meta")
    state = data.get("state") if isinstance(data, Mapping) else meta.get("operation_state") if isinstance(meta, Mapping) else None
    if not isinstance(state, str) or state not in {item.value for item in OperationState}:
        raise OnboardingError("The operation response did not contain a known state")
    return state


def _media_receipt_error_code(error: Exception) -> str:
    if isinstance(error, ExceptionGroup):
        codes = {_media_receipt_error_code(item) for item in error.exceptions}
        if codes and codes <= {"NETWORK_ERROR", "MCP_ERROR"}:
            return "MCP_ERROR" if "MCP_ERROR" in codes else "NETWORK_ERROR"
        return "INTERNAL_ERROR"
    if isinstance(error, MCPError):
        return "MCP_ERROR"
    if isinstance(error, (ConnectionError, OSError, EOFError)):
        return "NETWORK_ERROR"
    if isinstance(error, (OnboardingError, ValueError, TypeError)):
        return "UPSTREAM_CHANGED"
    return "INTERNAL_ERROR"


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
    for action in ("music", "video", "operation"):
        media = subparsers.add_parser(action, help="Start once or recover media by opaque operation handle")
        media.add_argument("--allow-live-account", action="store_true", required=True)
        media.add_argument("--timeout-seconds", type=int, choices=range(1, 1801), metavar="1..1800", default=720)
        if action == "operation":
            media.add_argument("--operation-id", required=True)
        else:
            media.add_argument("--prompt", required=True)
            media.add_argument("--output-dir", type=Path, required=True)
            media.add_argument("--model", default="flash")
            media.add_argument("--thinking-level", choices=("standard", "extended"), default="standard")
            media.add_argument("--idempotency-key", help="Reuse an opaque token after response loss; never use prompt text")
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
        elif action in {"music", "video"}:
            payload = asyncio.run(run_media_operation(
                args.prompt, media_type=action, output_dir=args.output_dir, model=args.model,
                thinking_level=args.thinking_level, timeout_seconds=args.timeout_seconds,
                idempotency_key=args.idempotency_key, server_command=args.server_command,
            ))
        elif action == "operation":
            payload = asyncio.run(run_media_operation(operation_id=args.operation_id,
                timeout_seconds=args.timeout_seconds, server_command=args.server_command))
        else:  # pragma: no cover - argparse constrains this branch.
            parser.error(f"Unsupported action: {action}")
    except (OnboardingError, TimeoutError) as exc:
        parser.exit(1, f"onboarding failed: {exc}\n")
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
