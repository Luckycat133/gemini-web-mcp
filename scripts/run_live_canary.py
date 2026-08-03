"""Run the explicitly enabled, privacy-bounded Gemini Web compatibility canary."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from src.services.compatibility import (
    DEPENDENCY_DISTRIBUTIONS,
    build_canary_report,
    probe_live_capabilities,
    render_report_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "compatibility" / "upstream-matrix.json"
DEFAULT_SCHEMA = PROJECT_ROOT / "compatibility" / "live-canary-report.schema.json"


def load_dependency_matrix(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("schema_version"), int):
        raise ValueError("compatibility matrix must be a versioned JSON object")
    dependencies = payload.get("dependencies")
    if not isinstance(dependencies, dict):
        raise ValueError("compatibility matrix dependencies must be an object")
    missing = [name for name in DEPENDENCY_DISTRIBUTIONS if name not in dependencies]
    if missing:
        raise ValueError("compatibility matrix is missing required dependencies")
    for name in DEPENDENCY_DISTRIBUTIONS:
        entry = dependencies[name]
        if not isinstance(entry, dict) or not isinstance(entry.get("requirement"), str):
            raise ValueError(f"compatibility matrix requirement for {name} must be a string")
    python_entry = payload.get("python")
    if not isinstance(python_entry, dict) or not isinstance(python_entry.get("supported"), str):
        raise ValueError("compatibility matrix python.supported must be a string")
    return payload


def load_report_validator(path: Path) -> Any:
    from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def fallback_dependency_matrix() -> dict[str, Any]:
    """Keep failure reporting schema-valid when the reviewed matrix cannot load."""

    return {
        "schema_version": 0,
        "python": {"supported": "unknown"},
        "dependencies": {
            distribution: {"requirement": "unknown"}
            for distribution in DEPENDENCY_DISTRIBUTIONS
        },
    }


def live_access_is_explicitly_enabled(arguments: argparse.Namespace, environment: Mapping[str, str]) -> bool:
    return bool(
        arguments.allow_live_account
        and environment.get("GEMINI_LIVE_CANARY_ENABLED", "").lower() == "true"
        and environment.get("GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT", "").lower() == "true"
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_summary(path: Path | None, report: Mapping[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(render_report_summary(report))


async def execute_canary(
    arguments: argparse.Namespace,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    values = os.environ if environment is None else environment
    trigger = values.get("GITHUB_EVENT_NAME") or "manual"
    commit = arguments.repository_commit or values.get("GITHUB_SHA")

    try:
        matrix = load_dependency_matrix(arguments.matrix)
    except Exception as error:
        return build_canary_report(
            matrix=fallback_dependency_matrix(),
            repository_commit=commit,
            trigger=trigger,
            status="failed",
            failure_stage="matrix",
            failure_error=error,
        )

    if not live_access_is_explicitly_enabled(arguments, values):
        return build_canary_report(
            matrix=matrix,
            repository_commit=commit,
            trigger=trigger,
            status="not_run",
            failure_stage="opt_in",
            failure_code="LIVE_OPT_IN_REQUIRED",
        )

    client: Any | None = None
    try:
        try:
            # Importing the account lifecycle is intentionally delayed until all
            # three opt-in controls have passed.
            from src.client_wrapper import get_gemini_client, initialize_client

            client = get_gemini_client()
            await initialize_client()
        except Exception as error:
            return build_canary_report(
                matrix=matrix,
                repository_commit=commit,
                trigger=trigger,
                client=client,
                status="failed",
                failure_stage="initialization",
                failure_error=error,
            )

        try:
            results = await probe_live_capabilities(
                client,
                timeout_seconds=arguments.probe_timeout_seconds,
            )
            return build_canary_report(
                matrix=matrix,
                repository_commit=commit,
                trigger=trigger,
                client=client,
                results=results,
            )
        except Exception as error:
            return build_canary_report(
                matrix=matrix,
                repository_commit=commit,
                trigger=trigger,
                client=client,
                status="failed",
                failure_stage="capability_probe",
                failure_error=error,
            )
    finally:
        if client is not None:
            try:
                from src.client_wrapper import reset_client_async

                await reset_client_async()
            except Exception:
                # The process is exiting and the report must stay privacy-bounded;
                # close exceptions are deliberately not serialized.
                pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-live-account",
        action="store_true",
        help="First opt-in gate; repository enable and dedicated-account flags are also required",
    )
    parser.add_argument("--output", type=Path, required=True, help="Path for the sanitized JSON report")
    parser.add_argument("--summary", type=Path, help="Optional Markdown summary path (appended)")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX, help="Reviewed upstream compatibility matrix")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Sanitized report JSON Schema")
    parser.add_argument("--repository-commit", help="Commit SHA; defaults to GITHUB_SHA")
    parser.add_argument("--probe-timeout-seconds", type=float, default=20.0)
    arguments = parser.parse_args(argv)
    if (
        not math.isfinite(arguments.probe_timeout_seconds)
        or arguments.probe_timeout_seconds <= 0
        or arguments.probe_timeout_seconds > 120
    ):
        parser.error("--probe-timeout-seconds must be within (0, 120]")
    return arguments


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_args(argv)
    try:
        validator = load_report_validator(arguments.schema)
    except Exception as error:
        report = build_canary_report(
            matrix=fallback_dependency_matrix(),
            repository_commit=arguments.repository_commit or os.environ.get("GITHUB_SHA"),
            trigger=os.environ.get("GITHUB_EVENT_NAME") or "manual",
            status="failed",
            failure_stage="report_schema",
            failure_error=error,
            failure_code="REPORT_SCHEMA_INVALID",
        )
        _write_report(arguments.output, report)
        _write_summary(arguments.summary, report)
        print(f"Live canary status: failed; sanitized report: {arguments.output}")
        return 1

    report = asyncio.run(execute_canary(arguments))
    try:
        validator.validate(report)
    except Exception as error:
        report = build_canary_report(
            matrix=fallback_dependency_matrix(),
            repository_commit=arguments.repository_commit or os.environ.get("GITHUB_SHA"),
            trigger=os.environ.get("GITHUB_EVENT_NAME") or "manual",
            status="failed",
            failure_stage="report_schema",
            failure_error=error,
            failure_code="REPORT_SCHEMA_INVALID",
        )
        validator.validate(report)
    _write_report(arguments.output, report)
    _write_summary(arguments.summary, report)
    print(f"Live canary status: {report['status']}; sanitized report: {arguments.output}")
    return 0 if report["status"] in {"healthy", "degraded"} else 1


if __name__ == "__main__":
    sys.exit(main())
