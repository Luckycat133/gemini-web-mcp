"""Shared account inventory and feature-probe services."""

from __future__ import annotations

from typing import Any

from ..infrastructure.rpc_contracts import RawRPCData, get_probe
from ..infrastructure.rpc_parsers import (
    extract_rpc_bodies,
    parse_library_capability,
    parse_public_link_entry,
    parse_tool_mode_entry,
    parse_usage_entry,
    summarize_rpc_response,
)


def sanitize_account_status(status: object) -> dict[str, Any]:
    if not isinstance(status, dict):
        return {"status": str(status)}
    summary_raw = status.get("summary")
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    rpc_raw = status.get("rpc")
    rpc = rpc_raw if isinstance(rpc_raw, dict) else {}
    rpc_status: dict[str, dict[str, Any]] = {}
    for name, payload in rpc.items():
        if not isinstance(payload, dict):
            rpc_status[name] = {"ok": bool(payload)}
            continue
        rpc_status[name] = {
            "ok": bool(payload.get("ok")),
            "status_code": payload.get("status_code"),
            "reject_code": payload.get("reject_code"),
        }
    return {
        "source_path": status.get("source_path"),
        "account_path": status.get("account_path"),
        "summary": summary,
        "rpc": rpc_status,
    }


async def inspect_account_status_compat(client: Any) -> dict[str, Any] | None:
    """Read the legacy inspection result or the newer SDK's cached status.

    The SDK initializes its enum to AVAILABLE even when the status RPC yields
    no parseable body, so this fallback cannot prove a fresh authenticated
    observation. Do not infer tier, features, or quota from the enum.
    """
    inspect = getattr(client, "inspect_account_status", None)
    if callable(inspect):
        return sanitize_account_status(await inspect())

    try:
        from gemini_webapi.constants import AccountStatus
    except ImportError:
        return None
    status = getattr(client, "account_status", None)
    if not isinstance(status, AccountStatus):
        return None
    return {
        "source_path": None,
        "account_path": None,
        "summary": {
            "account_status": status.name,
            "account_status_code": int(status),
            "account_status_description": status.description,
            "status_evidence": "sdk_cached_unverified",
        },
        "rpc": {},
    }


async def execute_observed_rpc(client: Any, probe: dict[str, str]) -> Any:
    """Execute a registry-derived probe; custom probes remain testable."""

    return await client._batch_execute(
        [RawRPCData(probe["rpcid"], probe["payload"])],
        source_path=probe["source_path"],
        close_on_error=False,
    )


# Compatibility aliases for existing in-process callers.
_sanitize_account_status = sanitize_account_status
_get_probe = get_probe
_execute_observed_rpc = execute_observed_rpc
_extract_rpc_bodies = extract_rpc_bodies
_summarize_probe_response = summarize_rpc_response
_parse_public_link_entry = parse_public_link_entry
_parse_usage_entry = parse_usage_entry
_parse_library_capability = parse_library_capability
_parse_tool_mode_entry = parse_tool_mode_entry
