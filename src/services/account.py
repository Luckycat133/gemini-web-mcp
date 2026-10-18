"""Shared account inventory and feature-probe services."""

from __future__ import annotations

from typing import Any, TypeVar

from ..domain import DomainErrorCode, DomainResult, new_diagnostic_id
from ..infrastructure.rpc_contracts import WEB_FEATURE_PROBE_CONTRACTS, RawRPCData, get_contract, get_probe
from ..infrastructure.rpc_parsers import (
    RPCParseResult,
    extract_rpc_bodies,
    parse_contract_body,
    parse_library_capability,
    parse_public_link_entry,
    parse_tool_mode_entry,
    parse_usage_entry,
    parse_rpc_envelope,
    summarize_rpc_response,
)

T = TypeVar("T")


def account_read_result(value: T, diagnostic: dict[str, Any]) -> DomainResult[T]:
    """Accept only a complete, validated inventory observation, including empty lists."""
    if (
        diagnostic.get("read_back_valid") is True
        and diagnostic.get("status_code") == 200
        and diagnostic.get("body_present") is True
        and diagnostic.get("reject_code") is None
        and diagnostic.get("parser_status") in {"success", "empty"}
        and not diagnostic.get("parser_warnings")
    ):
        return DomainResult.success(value, verification_status="observed", details=diagnostic)
    code = DomainErrorCode.UPSTREAM_REJECTED if diagnostic.get("reject_code") is not None else DomainErrorCode.UPSTREAM_CHANGED
    return DomainResult.failure(
        code, "The account response could not be verified.", retryable=False,
        verification_status="read_back_unavailable", details=diagnostic,
        diagnostic_id=new_diagnostic_id(),
    )


def account_contract_result(response: Any, key: str, **arguments: Any) -> DomainResult[dict[str, Any]]:
    """Validate HTTP, a unique matching envelope and its registered body parser once."""
    contract = get_contract(key)
    text = str(getattr(response, "text", "") or "")
    envelope = parse_rpc_envelope(text, contract.rpc_id)
    if envelope.reject_code is not None:
        parsed = RPCParseResult("rejected", reject_code=envelope.reject_code)
    elif envelope.single_body:
        parsed = parse_contract_body(contract, envelope.bodies[0], **arguments)
    else:
        parsed = RPCParseResult("changed_shape", warnings=("missing_or_ambiguous_body",))
    diagnostic = {
        "source_rpc": contract.rpc_id, "contract_key": key,
        "status_code": getattr(response, "status_code", None),
        "parsed": envelope.parsed, "response_parts": envelope.response_parts,
        "body_present": bool(envelope.bodies), "body_count": len(envelope.bodies),
        "matching_parts": envelope.matching_parts,
        "reject_code": envelope.reject_code, "response_length": len(text),
        "parser_status": parsed.status, "parser_warnings": list(parsed.warnings),
        "read_back_valid": (getattr(response, "status_code", None) == 200 and envelope.parsed
                            and envelope.reject_code is None and envelope.single_body
                            and parsed.ok and not parsed.warnings),
    }
    value = {"value": parsed.value}
    if key == "tool_modes.status" and envelope.single_body:
        body = envelope.bodies[0]
        value["leading_enabled"] = body[0] if isinstance(body, list) and body and type(body[0]) is bool else None
    return account_read_result(value, diagnostic)


def account_probe_status(response: Any, probe: dict[str, str]) -> dict[str, Any]:
    """Report probe reachability only after matching envelope/body validation."""
    contract = next((item for item in WEB_FEATURE_PROBE_CONTRACTS
                     if item.rpc_id == probe["rpcid"] and item.name == probe["name"] and item.surface == probe["surface"]), None)
    if contract is not None:
        result = account_contract_result(response, contract.key)
        diagnostic = dict(result.meta.details)
        ok = result.ok
    else:
        # Custom probes have no registered body schema, but still need one
        # matching response body; an HTTP 200 or an unrelated RPC is not proof.
        envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), probe["rpcid"])
        ok = (getattr(response, "status_code", None) == 200 and envelope.parsed
              and envelope.reject_code is None and envelope.single_body)
        diagnostic = {
            "status_code": getattr(response, "status_code", None), "parsed": envelope.parsed,
            "response_parts": envelope.response_parts, "body_count": len(envelope.bodies),
            "body_present": bool(envelope.bodies), "reject_code": envelope.reject_code,
        }
    diagnostic.update({"ok": ok, "reachable": ok})
    if not ok:
        diagnostic["error_code"] = (
            DomainErrorCode.UPSTREAM_REJECTED.value if diagnostic.get("reject_code") is not None
            else DomainErrorCode.UPSTREAM_CHANGED.value
        )
    return diagnostic


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
