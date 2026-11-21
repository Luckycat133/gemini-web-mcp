"""Privacy-bounded diagnostics for the opt-in Gemini Web live canary."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import platform
import re
from typing import Any, Literal, Mapping, Sequence

from ..infrastructure.rpc_contracts import (
    RPCContract,
    WEB_FEATURE_PROBE_CONTRACTS,
    execute_contract,
)
from ..infrastructure.rpc_parsers import parse_contract_body, parse_rpc_envelope


REPORT_SCHEMA_VERSION = 1
DEPENDENCY_DISTRIBUTIONS = (
    "gemini-mcp-server",
    "gemini-webapi",
    "mcp",
    "mcp-types",
)

CompatibilityState = Literal["compatible", "unavailable", "drift", "failed"]
CapabilityState = Literal["available", "empty", "unavailable", "unknown"]
ProbeOutcome = Literal[
    "available",
    "empty",
    "unavailable",
    "transport_failed",
    "envelope_drift",
    "parser_drift",
]
ParserStage = Literal["transport", "envelope", "rpc", "parser", "complete"]
TerminalState = Literal["completed", "rejected", "failed"]
ReportStatus = Literal["healthy", "degraded", "drift", "failed", "not_run"]

_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
_BUILD_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,127}$")
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
_COMMIT = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass(frozen=True, slots=True)
class CapabilityProbeResult:
    """One live probe outcome with no response body or account data."""

    capability: str
    surface: str
    name: str
    rpc_id: str
    source_path: str
    parser: str
    stability: str
    expected_dependency: str
    compatibility: CompatibilityState
    capability_state: CapabilityState
    outcome: ProbeOutcome
    terminal_state: TerminalState
    parser_stage: ParserStage
    status_code: int | None = None
    reject_code: int | None = None
    response_parts: int = 0
    body_count: int = 0
    warning_count: int = 0
    error_code: str | None = None
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitized_error_type(error: BaseException) -> str:
    """Return only a bounded exception class name, never its message."""

    name = type(error).__name__
    return name if _ERROR_TYPE.fullmatch(name) else "Exception"


def sanitized_error_code(error: BaseException) -> str:
    """Map an exception to a stable public code without serializing details."""

    lowered = sanitized_error_type(error).lower()
    if "auth" in lowered or "cookie" in lowered or "credential" in lowered:
        return "AUTHENTICATION_FAILED"
    if "timeout" in lowered:
        return "TRANSPORT_TIMEOUT"
    if "ratelimit" in lowered or "quota" in lowered:
        return "RATE_LIMITED"
    if "connection" in lowered or "network" in lowered or "socket" in lowered:
        return "NETWORK_ERROR"
    if isinstance(error, (ValueError, KeyError)):
        return "CONFIGURATION_ERROR"
    return "TRANSPORT_ERROR"


def _base_probe(contract: RPCContract) -> dict[str, Any]:
    return {
        "capability": contract.key,
        "surface": contract.surface,
        "name": contract.name,
        "rpc_id": contract.rpc_id,
        "source_path": contract.source_path,
        "parser": contract.parser,
        "stability": contract.stability,
        "expected_dependency": contract.verified_dependency,
    }


def failed_probe(contract: RPCContract, error: BaseException) -> CapabilityProbeResult:
    """Describe a transport exception without including its message."""

    return CapabilityProbeResult(
        **_base_probe(contract),
        compatibility="failed",
        capability_state="unknown",
        outcome="transport_failed",
        terminal_state="failed",
        parser_stage="transport",
        error_code=sanitized_error_code(error),
        error_type=sanitized_error_type(error),
    )


def assess_probe_response(contract: RPCContract, response: Any) -> CapabilityProbeResult:
    """Classify a live response while retaining only structural diagnostics."""

    status_value = getattr(response, "status_code", None)
    status_code = status_value if isinstance(status_value, int) else None
    if status_code != 200:
        return CapabilityProbeResult(
            **_base_probe(contract),
            compatibility="failed",
            capability_state="unknown",
            outcome="transport_failed",
            terminal_state="failed",
            parser_stage="transport",
            status_code=status_code,
            error_code="HTTP_STATUS",
        )

    response_text = getattr(response, "text", "")
    if not isinstance(response_text, str):
        response_text = ""
    envelope = parse_rpc_envelope(response_text, contract.rpc_id)
    if not envelope.parsed:
        return CapabilityProbeResult(
            **_base_probe(contract),
            compatibility="drift",
            capability_state="unknown",
            outcome="envelope_drift",
            terminal_state="failed",
            parser_stage="envelope",
            status_code=status_code,
            error_code="RPC_ENVELOPE_UNPARSEABLE",
        )

    if envelope.reject_code is not None:
        return CapabilityProbeResult(
            **_base_probe(contract),
            compatibility="unavailable",
            capability_state="unavailable",
            outcome="unavailable",
            terminal_state="rejected",
            parser_stage="rpc",
            status_code=status_code,
            reject_code=envelope.reject_code,
            response_parts=envelope.response_parts,
            body_count=len(envelope.bodies),
            error_code="RPC_REJECTED",
        )

    if not envelope.bodies:
        return CapabilityProbeResult(
            **_base_probe(contract),
            compatibility="drift",
            capability_state="unknown",
            outcome="envelope_drift",
            terminal_state="failed",
            parser_stage="envelope",
            status_code=status_code,
            response_parts=envelope.response_parts,
            error_code="RPC_BODY_MISSING",
        )

    parsed = parse_contract_body(contract, envelope.bodies[0])
    if parsed.status == "changed_shape":
        return CapabilityProbeResult(
            **_base_probe(contract),
            compatibility="drift",
            capability_state="unknown",
            outcome="parser_drift",
            terminal_state="failed",
            parser_stage="parser",
            status_code=status_code,
            response_parts=envelope.response_parts,
            body_count=len(envelope.bodies),
            warning_count=len(parsed.warnings),
            error_code="RPC_PARSER_CHANGED_SHAPE",
        )

    capability_state: CapabilityState = "empty" if parsed.status == "empty" else "available"
    outcome: ProbeOutcome = "empty" if parsed.status == "empty" else "available"
    return CapabilityProbeResult(
        **_base_probe(contract),
        compatibility="compatible",
        capability_state=capability_state,
        outcome=outcome,
        terminal_state="completed",
        parser_stage="complete",
        status_code=status_code,
        response_parts=envelope.response_parts,
        body_count=len(envelope.bodies),
        warning_count=len(parsed.warnings),
    )


async def probe_live_capabilities(
    client: Any,
    *,
    contracts: Sequence[RPCContract] = WEB_FEATURE_PROBE_CONTRACTS,
    timeout_seconds: float = 20.0,
) -> list[CapabilityProbeResult]:
    """Run the centralized read-only contracts sequentially against one account."""

    results: list[CapabilityProbeResult] = []
    for contract in contracts:
        try:
            async with asyncio.timeout(timeout_seconds):
                response = await execute_contract(client, contract.key)
            results.append(assess_probe_response(contract, response))
        except Exception as error:
            results.append(failed_probe(contract, error))
    return results


def _safe_client_value(client: Any, attribute: str, pattern: re.Pattern[str]) -> str | None:
    try:
        value = getattr(client, attribute, None)
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if pattern.fullmatch(stripped) else None


def sanitized_web_metadata(client: Any) -> dict[str, str | None]:
    """Read only the allowlisted Web build and locale diagnostics."""

    return {
        "locale": _safe_client_value(client, "language", _LOCALE),
        "build_label": _safe_client_value(client, "build_label", _BUILD_LABEL),
    }


def _installed_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "not-installed"


def dependency_snapshot(matrix: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """Combine installed versions with reviewed supported ranges."""

    dependencies = matrix.get("dependencies")
    if not isinstance(dependencies, Mapping):
        raise ValueError("compatibility matrix dependencies must be an object")

    python_entry = matrix.get("python")
    python_supported = python_entry.get("supported") if isinstance(python_entry, Mapping) else None
    snapshot: dict[str, dict[str, str]] = {
        "python": {
            "installed": platform.python_version(),
            "supported": str(python_supported or "unknown"),
        }
    }
    for distribution in DEPENDENCY_DISTRIBUTIONS:
        entry = dependencies.get(distribution)
        if not isinstance(entry, Mapping) or not isinstance(entry.get("requirement"), str):
            raise ValueError(f"compatibility matrix missing requirement for {distribution}")
        snapshot[distribution] = {
            "installed": _installed_version(distribution),
            "supported": str(entry["requirement"]),
        }
    return snapshot


def _safe_commit(value: str | None) -> str:
    candidate = (value or "").strip()
    return candidate.lower() if _COMMIT.fullmatch(candidate) else "unknown"


def _safe_trigger(value: str | None) -> str:
    return value if value in {"schedule", "workflow_dispatch", "manual", "test"} else "unknown"


def _summary(results: Sequence[CapabilityProbeResult]) -> dict[str, int]:
    return {
        "total": len(results),
        "available": sum(item.outcome == "available" for item in results),
        "empty": sum(item.outcome == "empty" for item in results),
        "unavailable": sum(item.outcome == "unavailable" for item in results),
        "drift": sum(item.compatibility == "drift" for item in results),
        "failed": sum(item.compatibility == "failed" for item in results),
    }


def _report_status(results: Sequence[CapabilityProbeResult]) -> ReportStatus:
    if any(item.compatibility == "drift" for item in results):
        return "drift"
    if any(item.compatibility == "failed" for item in results):
        return "failed"
    if any(item.compatibility == "unavailable" for item in results):
        return "degraded"
    return "healthy"


def build_canary_report(
    *,
    matrix: Mapping[str, Any],
    repository_commit: str | None,
    trigger: str | None,
    client: Any | None = None,
    results: Sequence[CapabilityProbeResult] = (),
    status: ReportStatus | None = None,
    failure_stage: str | None = None,
    failure_error: BaseException | None = None,
    failure_code: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the only JSON shape the workflow is allowed to persist or publish."""

    resolved_status = status or _report_status(results)
    failure: dict[str, str] | None = None
    if failure_stage is not None:
        failure = {
            "stage": failure_stage,
            "error_code": failure_code or (
                sanitized_error_code(failure_error) if failure_error is not None else "CANARY_FAILED"
            ),
            "error_type": sanitized_error_type(failure_error) if failure_error is not None else "CanaryError",
        }

    timestamp = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "matrix_version": int(matrix.get("schema_version", 0)),
        "generated_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "repository_commit": _safe_commit(repository_commit),
        "trigger": _safe_trigger(trigger),
        "status": resolved_status,
        "terminal_state": "completed" if resolved_status in {"healthy", "degraded"} else "failed",
        "dependencies": dependency_snapshot(matrix),
        "web": sanitized_web_metadata(client) if client is not None else {"locale": None, "build_label": None},
        "summary": _summary(results),
        "capabilities": [item.to_dict() for item in results],
        "failure": failure,
        "privacy": {
            "raw_responses_included": False,
            "account_content_included": False,
            "credentials_included": False,
        },
    }


def render_report_summary(report: Mapping[str, Any]) -> str:
    """Render a bounded Markdown summary without account or response data."""

    summary_value = report.get("summary")
    summary: Mapping[str, Any] = summary_value if isinstance(summary_value, Mapping) else {}
    web_value = report.get("web")
    web: Mapping[str, Any] = web_value if isinstance(web_value, Mapping) else {}
    lines = [
        "## Gemini Web compatibility canary",
        "",
        f"- Status: `{report.get('status', 'failed')}`",
        f"- Commit: `{report.get('repository_commit', 'unknown')}`",
        f"- Web build: `{web.get('build_label') or 'unavailable'}`",
        f"- Locale: `{web.get('locale') or 'unavailable'}`",
        (
            f"- Capabilities: {summary.get('available', 0)} available, {summary.get('empty', 0)} empty, "
            f"{summary.get('unavailable', 0)} unavailable, {summary.get('drift', 0)} drift, "
            f"{summary.get('failed', 0)} failed"
        ),
        "- Privacy: raw responses, account content, credentials, and session identifiers are omitted.",
    ]
    return "\n".join(lines) + "\n"
