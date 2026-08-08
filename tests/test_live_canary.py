"""Offline contracts for the opt-in Gemini Web live compatibility canary."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import tomllib

from jsonschema import ValidationError, validate
import pytest

from scripts.run_live_canary import (
    execute_canary,
    live_access_is_explicitly_enabled,
    load_dependency_matrix,
    load_report_validator,
)
from src.infrastructure.rpc_contracts import WEB_FEATURE_PROBE_CONTRACTS, get_contract
from src.services.compatibility import (
    assess_probe_response,
    build_canary_report,
    probe_live_capabilities,
    render_report_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "live_canary_cases.json"
MATRIX_PATH = PROJECT_ROOT / "compatibility" / "upstream-matrix.json"
SCHEMA_PATH = PROJECT_ROOT / "compatibility" / "live-canary-report.schema.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
MATRIX = load_dependency_matrix(MATRIX_PATH)
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case_name", sorted(FIXTURE["cases"]))
def test_synthetic_fixture_classifies_transport_envelope_rpc_and_parser_stages(case_name: str) -> None:
    assert FIXTURE["synthetic_only"] is True
    case = FIXTURE["cases"][case_name]
    response = SimpleNamespace(status_code=case["status_code"], text=case["response_text"])

    result = assess_probe_response(get_contract(FIXTURE["contract_key"]), response)

    assert result.outcome == case["expected_outcome"]
    assert result.parser_stage == case["expected_stage"]
    assert "synthetic-item" not in json.dumps(result.to_dict())
    assert "synthetic-body-that-must-not-be-reported" not in json.dumps(result.to_dict())


def test_probe_exception_reports_only_sanitized_type_and_code() -> None:
    secret = "PSID=private-cookie account-message=private-chat"

    class FakeClient:
        async def _batch_execute(self, *_args, **_kwargs):
            raise ConnectionError(secret)

    result = asyncio.run(
        probe_live_capabilities(
            FakeClient(),
            contracts=(get_contract("library.index"),),
            timeout_seconds=1,
        )
    )[0]
    serialized = json.dumps(result.to_dict())

    assert result.error_code == "NETWORK_ERROR"
    assert result.error_type == "ConnectionError"
    assert secret not in serialized
    assert "private-cookie" not in serialized
    assert "private-chat" not in serialized


def test_report_is_schema_valid_and_only_reads_allowlisted_client_metadata() -> None:
    case = FIXTURE["cases"]["available"]
    result = assess_probe_response(
        get_contract(FIXTURE["contract_key"]),
        SimpleNamespace(status_code=case["status_code"], text=case["response_text"]),
    )
    client = SimpleNamespace(
        language="zh-CN",
        build_label="boq_assistant-bard-web-server_20260803.01_p0",
        session_id="session-private-123",
        cookies={"__Secure-1PSID": "cookie-private-456"},
        account_content="chat-private-789",
    )
    report = build_canary_report(
        matrix=MATRIX,
        repository_commit="a" * 40,
        trigger="test",
        client=client,
        results=(result,),
        generated_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    validate(report, SCHEMA)
    serialized = json.dumps(report)
    assert report["status"] == "healthy"
    assert report["web"] == {
        "locale": "zh-CN",
        "build_label": "boq_assistant-bard-web-server_20260803.01_p0",
    }
    for forbidden in ("session-private-123", "cookie-private-456", "chat-private-789", "__Secure-1PSID"):
        assert forbidden not in serialized


def test_schema_rejects_unreviewed_report_fields() -> None:
    report = build_canary_report(
        matrix=MATRIX,
        repository_commit=None,
        trigger="test",
        status="not_run",
        failure_stage="opt_in",
        failure_code="LIVE_OPT_IN_REQUIRED",
    )
    report["raw_response"] = "private account content"

    with pytest.raises(ValidationError):
        validate(report, SCHEMA)


def test_rejections_are_degraded_capabilities_while_parser_changes_are_drift() -> None:
    contract = get_contract(FIXTURE["contract_key"])
    rejected_case = FIXTURE["cases"]["rejected"]
    drift_case = FIXTURE["cases"]["parser_drift"]
    rejected = assess_probe_response(
        contract,
        SimpleNamespace(status_code=rejected_case["status_code"], text=rejected_case["response_text"]),
    )
    drift = assess_probe_response(
        contract,
        SimpleNamespace(status_code=drift_case["status_code"], text=drift_case["response_text"]),
    )

    degraded_report = build_canary_report(
        matrix=MATRIX,
        repository_commit="b" * 40,
        trigger="test",
        results=(rejected,),
    )
    drift_report = build_canary_report(
        matrix=MATRIX,
        repository_commit="b" * 40,
        trigger="test",
        results=(rejected, drift),
    )

    assert degraded_report["status"] == "degraded"
    assert degraded_report["terminal_state"] == "completed"
    assert drift_report["status"] == "drift"
    assert drift_report["summary"]["drift"] == 1
    assert drift_report["terminal_state"] == "failed"


def test_invalid_build_and_locale_values_are_omitted() -> None:
    client = SimpleNamespace(
        language="zh-CN\nprivate-chat",
        build_label="build label with spaces and account content",
    )
    report = build_canary_report(
        matrix=MATRIX,
        repository_commit="not a commit",
        trigger="untrusted-trigger",
        client=client,
    )

    assert report["repository_commit"] == "unknown"
    assert report["trigger"] == "unknown"
    assert report["web"] == {"locale": None, "build_label": None}


def test_dependency_matrix_matches_pyproject_and_the_probe_registry_is_read_only() -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    requirements = {item.split(">", 1)[0]: item for item in project["dependencies"]}

    for distribution in ("gemini-webapi", "mcp", "mcp-types"):
        assert MATRIX["dependencies"][distribution]["requirement"] == requirements[distribution]
    assert MATRIX["python"]["supported"] == project["requires-python"]
    assert MATRIX["evidence"]["live"] == "not_observed_in_this_change"
    assert len(WEB_FEATURE_PROBE_CONTRACTS) == 21
    assert all(contract.mode == "read" for contract in WEB_FEATURE_PROBE_CONTRACTS)


@pytest.mark.parametrize(
    ("allow_flag", "enabled", "dedicated", "expected"),
    [
        (False, "true", "true", False),
        (True, "false", "true", False),
        (True, "true", "false", False),
        (True, "true", "true", True),
    ],
)
def test_live_access_requires_all_three_opt_in_controls(
    allow_flag: bool,
    enabled: str,
    dedicated: str,
    expected: bool,
) -> None:
    arguments = SimpleNamespace(allow_live_account=allow_flag)
    environment = {
        "GEMINI_LIVE_CANARY_ENABLED": enabled,
        "GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT": dedicated,
    }

    assert live_access_is_explicitly_enabled(arguments, environment) is expected


def test_cli_without_explicit_opt_in_refuses_network_and_writes_sanitized_report(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    environment = os.environ.copy()
    environment.update(
        {
            "GEMINI_LIVE_CANARY_ENABLED": "true",
            "GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT": "true",
            "GEMINI_PSID": "private-cookie-must-never-appear",
        }
    )
    completed = subprocess.run(
        [sys.executable, "scripts/run_live_canary.py", "--output", str(report_path)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    validate(report, SCHEMA)
    assert completed.returncode == 1
    assert report["status"] == "not_run"
    assert report["failure"]["error_code"] == "LIVE_OPT_IN_REQUIRED"
    assert "private-cookie-must-never-appear" not in report_text + completed.stdout + completed.stderr


def test_initialization_failure_is_sanitized_and_retires_the_client(monkeypatch) -> None:
    import src.client_wrapper as client_wrapper

    private_message = "private-cookie and private-account-content"
    retired = False

    class AuthError(Exception):
        pass

    client = SimpleNamespace(
        language="en-US",
        build_label="boq_assistant-bard-web-server_20260803.02_p0",
        session_id="private-session",
    )

    async def fail_initialize():
        raise AuthError(private_message)

    async def retire_client():
        nonlocal retired
        retired = True
        return None

    monkeypatch.setattr(client_wrapper, "get_gemini_client", lambda: client)
    monkeypatch.setattr(client_wrapper, "initialize_client", fail_initialize)
    monkeypatch.setattr(client_wrapper, "reset_client_async", retire_client)
    arguments = SimpleNamespace(
        allow_live_account=True,
        matrix=MATRIX_PATH,
        repository_commit="e" * 40,
        probe_timeout_seconds=1.0,
    )
    report = asyncio.run(
        execute_canary(
            arguments,
            environment={
                "GEMINI_LIVE_CANARY_ENABLED": "true",
                "GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT": "true",
                "GITHUB_EVENT_NAME": "test",
            },
        )
    )

    validate(report, SCHEMA)
    serialized = json.dumps(report)
    assert report["failure"] == {
        "stage": "initialization",
        "error_code": "AUTHENTICATION_FAILED",
        "error_type": "AuthError",
    }
    assert retired is True
    assert private_message not in serialized
    assert "private-session" not in serialized


def test_invalid_report_schema_blocks_live_execution_before_client_import(tmp_path: Path) -> None:
    report_path = tmp_path / "schema-failure.json"
    environment = os.environ.copy()
    environment.update(
        {
            "GEMINI_LIVE_CANARY_ENABLED": "true",
            "GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT": "true",
            "GEMINI_PSID": "private-cookie-must-never-appear",
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_live_canary.py",
            "--allow-live-account",
            "--schema",
            str(tmp_path / "missing-schema.json"),
            "--output",
            str(report_path),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert completed.returncode == 1
    assert report["failure"]["stage"] == "report_schema"
    assert report["failure"]["error_code"] == "REPORT_SCHEMA_INVALID"
    assert "private-cookie-must-never-appear" not in report_text + completed.stdout + completed.stderr


def test_report_validator_rejects_content_outside_the_allowlist() -> None:
    validator = load_report_validator(SCHEMA_PATH)
    report = build_canary_report(
        matrix=MATRIX,
        repository_commit="d" * 40,
        trigger="test",
    )
    validator.validate(report)
    report["response_text"] = "private-chat"

    with pytest.raises(ValidationError):
        validator.validate(report)


def test_markdown_summary_contains_only_bounded_diagnostics() -> None:
    report = build_canary_report(
        matrix=MATRIX,
        repository_commit="c" * 40,
        trigger="test",
        status="not_run",
        failure_stage="opt_in",
        failure_code="LIVE_OPT_IN_REQUIRED",
    )
    summary = render_report_summary(report)

    assert "Status: `not_run`" in summary
    assert "raw responses, account content, credentials, and session identifiers are omitted" in summary
    unsafe = deepcopy(report)
    unsafe["unreviewed"] = "private-chat"
    assert "private-chat" not in render_report_summary(unsafe)
