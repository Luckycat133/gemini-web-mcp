"""Opt-in media onboarding: one start, bounded recovery and byte verification."""

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import MCPError

import src.onboarding as onboarding
from src.adapters.mcp_sdk import CallToolResult, TextContent
from tests.media_fixtures import write_audio


def _result(state, *, artifacts=(), operation="music"):
    return CallToolResult(content=[TextContent(type="text", text="fixture")], structuredContent={
        "ok": True, "error": None, "warnings": [], "meta": {"operation_state": state},
        "data": {"operation_id": "op_fixture", "state": state, "operation": operation,
                 "artifacts": list(artifacts), "source_chat_id": "c_private_fixture"},
    })


def _audio(path):
    write_audio(path)
    return {"kind": "audio", "local_path": str(path), "verification": {"status": "verified"}}


def _client(monkeypatch, responses, *, expire_at=None, enter_error=None, exit_error=None):
    calls = []
    deadlines = []
    actual_timeout = asyncio.timeout

    def timeout(_seconds):
        deadline = actual_timeout(None)
        deadlines.append(deadline)
        return deadline

    async def expire():
        deadlines[0].reschedule(asyncio.get_running_loop().time())
        await asyncio.Event().wait()

    class Client:
        server_info = SimpleNamespace(version="fixture")
        protocol_version = "fixture"

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            if enter_error is not None:
                raise enter_error
            if expire_at == "enter":
                await expire()
            return self

        async def __aexit__(self, *_args):
            if exit_error is not None:
                raise exit_error

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if expire_at == name:
                await expire()
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

    monkeypatch.setattr(onboarding, "Client", Client)
    monkeypatch.setattr(onboarding, "stdio_client", lambda _parameters: object())
    monkeypatch.setattr(onboarding, "_resolve_server_command", lambda _command: "/sealed/gemini-mcp-create")
    monkeypatch.setattr(onboarding, "_server_environment", lambda **_kwargs: {})
    monkeypatch.setattr(onboarding.asyncio, "timeout", timeout)
    monkeypatch.setattr(onboarding.asyncio, "sleep", AsyncMock())
    return calls


def test_media_onboarding_starts_once_polls_and_independently_hashes_local_file(monkeypatch, tmp_path):
    path = tmp_path / "song.wav"
    calls = _client(monkeypatch, [_result("accepted"), _result("queued"), _result("completed", artifacts=[_audio(path)])])
    payload = asyncio.run(onboarding.run_media_operation("private prompt", output_dir=tmp_path, idempotency_key="start_1"))
    assert [name for name, _args in calls] == ["gemini_generate_music", "gemini_get_operation_result", "gemini_get_operation_result"]
    assert calls[0][1]["idempotency_key"] == "start_1"
    assert calls[0][1]["output_dir"] == str(tmp_path.resolve())
    assert payload["status"] == "ok" and payload["state"] == "completed"
    assert payload["artifacts"][0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert payload["artifacts"][0]["duration_seconds"] == 1.0
    assert "c_private_fixture" not in str(payload) and "private prompt" not in str(payload)
    assert payload["generation_resubmitted"] is False


def test_operation_onboarding_recovers_existing_handle_without_a_start(monkeypatch, tmp_path):
    calls = _client(monkeypatch, [_result("completed", artifacts=[_audio(tmp_path / "ready.wav")])])
    payload = asyncio.run(onboarding.run_media_operation(operation_id="op_fixture"))
    assert calls == [("gemini_get_operation_result", {"operation_id": "op_fixture"})]
    assert payload["status"] == "ok" and payload["idempotency_key"] is None


def _transport_failure(kind):
    private = "private response c_private_fixture credential_fixture"
    if kind == "connection":
        return ConnectionError(private), "NETWORK_ERROR"
    if kind == "mcp":
        return MCPError(-32000, private, data={"credential": private}), "MCP_ERROR"
    return ExceptionGroup(private, [ConnectionError(private), MCPError(-32000, private)]), "MCP_ERROR"


@pytest.mark.parametrize("kind", ["connection", "mcp", "group"])
def test_lost_start_response_retains_generated_key_without_resubmitting(monkeypatch, kind):
    error, code = _transport_failure(kind)
    calls = _client(monkeypatch, [error])
    payload = asyncio.run(onboarding.run_media_operation("private prompt", media_type="video"))
    assert len(calls) == 1 and calls[0][0] == "gemini_generate_video"
    assert payload["idempotency_key"] == calls[0][1]["idempotency_key"]
    assert len(payload["idempotency_key"]) == 32
    assert payload["operation_id"] is None
    assert payload["status"] == "pending" and payload["state"] == "unknown"
    assert payload["error_code"] == code and payload["failure_stage"] == "start"
    assert payload["start_attempted"] is True and payload["completion_verified"] is False
    assert "same authentication context" in payload["next_step"] and "do not use a new key" in payload["next_step"]
    assert payload["generation_resubmitted"] is False
    assert all(value not in str(payload) for value in ("private prompt", "private response", "c_private_fixture", "credential_fixture"))


@pytest.mark.parametrize("kind", ["connection", "mcp", "group"])
def test_lost_result_response_retains_observed_handle_and_initial_key(monkeypatch, kind):
    error, code = _transport_failure(kind)
    calls = _client(monkeypatch, [_result("accepted"), error])
    payload = asyncio.run(onboarding.run_media_operation("private prompt"))
    assert [name for name, _args in calls] == ["gemini_generate_music", "gemini_get_operation_result"]
    assert payload["operation_id"] == "op_fixture"
    assert payload["idempotency_key"] == calls[0][1]["idempotency_key"]
    assert payload["status"] == "pending" and payload["state"] == "partial"
    assert payload["last_observed_state"] == "accepted"
    assert payload["failure_stage"] == "result" and payload["error_code"] == code
    assert "operation --allow-live-account" in payload["next_step"]
    assert payload["generation_resubmitted"] is False
    assert all(value not in str(payload) for value in ("private prompt", "private response", "c_private_fixture", "credential_fixture"))


@pytest.mark.parametrize("kind", ["connection", "mcp"])
@pytest.mark.parametrize("key", [None, "initial_start"])
def test_disconnected_recovery_retains_existing_handle_and_never_starts(monkeypatch, kind, key):
    error, code = _transport_failure(kind)
    calls = _client(monkeypatch, [error])
    payload = asyncio.run(onboarding.run_media_operation(operation_id="op_fixture", idempotency_key=key))
    assert calls == [("gemini_get_operation_result", {"operation_id": "op_fixture"})]
    assert payload["operation_id"] == "op_fixture" and payload["idempotency_key"] == key
    assert payload["status"] == "pending" and payload["error_code"] == code
    assert payload["start_attempted"] is False


@pytest.mark.parametrize("malformed", [
    CallToolResult(content=[TextContent(type="text", text="private response c_private_fixture")]),
    _result("private response c_private_fixture"),
    _result("running", operation={"credential": "private response c_private_fixture"}),
])
def test_invalid_result_response_retains_known_recovery_identifiers(monkeypatch, malformed):
    calls = _client(monkeypatch, [_result("accepted"), malformed])
    payload = asyncio.run(onboarding.run_media_operation("private prompt", idempotency_key="initial_start"))
    assert len(calls) == 2 and calls[0][0] == "gemini_generate_music"
    assert payload["operation_id"] == "op_fixture" and payload["idempotency_key"] == "initial_start"
    assert payload["status"] == "pending" and payload["state"] == "partial"
    assert payload["error_code"] == "UPSTREAM_CHANGED" and payload["failure_stage"] == "result_response"
    assert "private response" not in str(payload) and "c_private_fixture" not in str(payload)


def test_file_verification_failure_returns_receipt_with_handle_instead_of_escaping(monkeypatch, tmp_path):
    path = tmp_path / "unverified.wav"
    path.write_bytes(b"not audio")
    artifact = {"kind": "audio", "local_path": str(path), "verification": {"status": "verified"}}
    calls = _client(monkeypatch, [_result("accepted"), _result("completed", artifacts=[artifact])])
    payload = asyncio.run(onboarding.run_media_operation("fixture", idempotency_key="initial_start"))
    assert [name for name, _args in calls] == ["gemini_generate_music", "gemini_get_operation_result"]
    assert payload["operation_id"] == "op_fixture" and payload["idempotency_key"] == "initial_start"
    assert payload["status"] == "failed" and payload["state"] == "failed"
    assert payload["error_code"] == "VERIFICATION_FAILED" and payload["failure_stage"] == "verification"
    assert payload["last_observed_state"] == "completed" and payload["completion_verified"] is False
    assert payload["artifacts"] == [] and "operation --allow-live-account" in payload["next_step"]


def test_verification_timeout_does_not_promote_claimed_completed_to_success(monkeypatch, tmp_path):
    calls = _client(monkeypatch, [_result("accepted"), _result("completed", artifacts=[_audio(tmp_path / "song.wav")])])

    def timeout(*_args, **_kwargs):
        raise TimeoutError("private verification diagnostic")

    monkeypatch.setattr(onboarding, "verify_local_media_artifacts", timeout)
    payload = asyncio.run(onboarding.run_media_operation("fixture", idempotency_key="initial_start"))
    assert len(calls) == 2 and payload["operation_id"] == "op_fixture"
    assert payload["idempotency_key"] == "initial_start"
    assert payload["status"] == "pending" and payload["state"] == "timed_out"
    assert payload["error_code"] == "TIMED_OUT" and payload["failure_stage"] == "verification"
    assert payload["last_observed_state"] == "completed" and payload["completion_verified"] is False
    assert payload["artifacts"] == [] and "private verification diagnostic" not in str(payload)


@pytest.mark.parametrize("kind", ["connection", "mcp"])
def test_shutdown_failure_preserves_independently_verified_artifacts_and_handle(monkeypatch, tmp_path, kind):
    path = tmp_path / "song.wav"
    error, code = _transport_failure(kind)
    calls = _client(monkeypatch, [_result("accepted"), _result("completed", artifacts=[_audio(path)])], exit_error=error)
    payload = asyncio.run(onboarding.run_media_operation("fixture", idempotency_key="initial_start"))
    assert len(calls) == 2 and payload["operation_id"] == "op_fixture"
    assert payload["idempotency_key"] == "initial_start"
    assert payload["status"] == "ok" and payload["state"] == "completed" and payload["completion_verified"] is True
    assert payload["error_code"] == code and payload["failure_stage"] == "shutdown"
    assert payload["artifacts"][0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "private response" not in str(payload) and payload["generation_resubmitted"] is False


def test_initialization_failure_retains_initial_key_but_does_not_claim_submission(monkeypatch):
    error, _code = _transport_failure("connection")
    calls = _client(monkeypatch, [], enter_error=error)
    payload = asyncio.run(onboarding.run_media_operation("fixture", idempotency_key="initial_start"))
    assert calls == [] and payload["operation_id"] is None and payload["idempotency_key"] == "initial_start"
    assert payload["start_attempted"] is False and payload["failure_stage"] == "initialize"
    assert payload["status"] == "pending" and payload["error_code"] == "NETWORK_ERROR"


def test_blank_start_prompt_is_rejected_before_client_initialization(monkeypatch):
    constructed = []
    monkeypatch.setattr(onboarding, "Client", lambda *_args, **_kwargs: constructed.append(True))
    with pytest.raises(onboarding.OnboardingError, match="nonblank prompt"):
        asyncio.run(onboarding.run_media_operation("  "))
    assert constructed == []


@pytest.mark.parametrize("phase,expected_calls", [("enter", []), ("gemini_generate_video", ["gemini_generate_video"]),
                                                   ("gemini_get_operation_result", ["gemini_generate_video", "gemini_get_operation_result"])])
def test_total_deadline_includes_initialization_and_preserves_response_loss_recovery(monkeypatch, phase, expected_calls):
    calls = _client(monkeypatch, [_result("accepted", operation="video")], expire_at=phase)
    payload = asyncio.run(onboarding.run_media_operation("fixture", media_type="video", idempotency_key="recover_start"))
    assert [name for name, _args in calls] == expected_calls
    assert payload["status"] == "pending" and payload["state"] == "timed_out"
    assert payload["idempotency_key"] == "recover_start"
    assert payload["generation_resubmitted"] is False
    if phase == "gemini_get_operation_result":
        assert payload["operation_id"] == "op_fixture" and "operation --allow-live-account" in payload["next_step"]
    else:
        assert payload["operation_id"] is None and "same start" in payload["next_step"]


def test_media_onboarding_does_not_trust_claimed_verification_or_wrong_modal_output(tmp_path):
    path = tmp_path / "fake.wav"
    path.write_bytes(b"not audio")
    with pytest.raises(onboarding.OnboardingError, match="could not be verified"):
        onboarding.verify_local_media_artifacts({"data": {"artifacts": [{
            "kind": "audio", "local_path": str(path), "verification": {"status": "verified"},
        }]}}, media_type="music")
    assert onboarding.verify_local_media_artifacts({"data": {"artifacts": [_audio(tmp_path / "real.wav")]}}, media_type="video") == []


@pytest.mark.parametrize("arguments", [["music", "--prompt", "x", "--output-dir", "out"],
                                         ["video", "--prompt", "x", "--output-dir", "out"],
                                         ["operation", "--operation-id", "op_fixture"]])
def test_music_video_and_recovery_require_explicit_live_account_opt_in(arguments):
    with pytest.raises(SystemExit):
        onboarding._build_parser().parse_args(arguments)


def test_focused_entrypoint_resolves_beside_current_python_before_other_environments(monkeypatch, tmp_path):
    python = tmp_path / "python"
    server = tmp_path / "gemini-mcp-create"
    server.touch()
    monkeypatch.setattr(onboarding.sys, "executable", str(python))
    monkeypatch.setattr(onboarding.shutil, "which", lambda _name: "/another/environment/gemini-mcp-create")
    assert Path(onboarding._resolve_server_command("gemini-mcp-create")) == server
