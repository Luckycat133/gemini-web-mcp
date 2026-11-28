"""Offline tests through the unified MCP client, not the handler signatures."""

import asyncio
from datetime import datetime, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator, ValidationError
from mcp import Client

from src.domain import DomainErrorCode, DomainResult, LongOperationData, OperationState
from src.onboarding import domain_result_from_call
from src.services import ChatOperationData, background, resilience
from src.surfaces import assist, function
from src.tools import operations, task_generation

HANDLE = "op_" + "a" * 32
CASES = {
    "ask-gemini": ({"prompt": "synthetic"}, {"prompt": "synthetic", "inputs": ["input"] * 17}),
    "generate-image": ({"prompt": "synthetic", "wait_seconds": 0}, {"prompt": "synthetic", "wait_seconds": 301}),
    "generate-video": ({"prompt": "synthetic", "wait_seconds": 0}, {"prompt": "synthetic", "duration_seconds": "10"}),
    "generate-music": ({"prompt": "synthetic", "wait_seconds": 0}, {"prompt": "synthetic", "genre": "unknown"}),
    "run-research": ({"query": "synthetic", "wait_seconds": 0}, {"query": "synthetic", "wait_seconds": "0"}),
    "run-task": ({"prompt": "synthetic", "wait_seconds": 0}, {"prompt": "synthetic", "retain_chat": "false"}),
    "get-operation": ({"operation_id": HANDLE, "status_only": True}, {"operation_id": HANDLE, "wait_seconds": True}),
    "check-quota": ({}, {"fixture_unknown_field": "fixture-private-marker"}),
}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_all_public_tools_enforce_input_and_match_output_schema(monkeypatch, mode):
    accepted = DomainResult.success(LongOperationData(
        operation="synthetic", state=OperationState.ACCEPTED, operation_id=HANDLE,
    ), operation_state=OperationState.ACCEPTED)
    reply = SimpleNamespace(text="synthetic answer", metadata=[], images=[], videos=[], media=[])
    stubs = {
        "ask-gemini": AsyncMock(return_value=DomainResult.success(
            ChatOperationData("hydrogen", "hydrogen", "gemini-4-hydrogen", response=reply),
        )),
        "generate-image": AsyncMock(return_value=accepted),
        "run-research": AsyncMock(return_value=accepted),
        "run-task": AsyncMock(return_value=accepted),
        "get-operation": AsyncMock(return_value=DomainResult.failure(
            DomainErrorCode.INVALID_ARGUMENT, "Synthetic handle absent.",
        )),
        "check-quota": AsyncMock(return_value=DomainResult.success({"synthetic": True})),
    }
    stubs["generate-video"] = stubs["generate-music"] = stubs["generate-image"]
    monkeypatch.setattr(function, "submit_media", stubs["generate-image"])
    monkeypatch.setattr(assist._chat_service, "generate_stream", stubs["ask-gemini"])
    monkeypatch.setattr(background, "submit_research", stubs["run-research"])
    monkeypatch.setattr(task_generation, "submit_task", stubs["run-task"])
    monkeypatch.setattr(operations, "_service", lambda: SimpleNamespace(get_status=stubs["get-operation"]))
    monkeypatch.setattr(function, "fetch_initialized_quota_summary", stubs["check-quota"])
    monkeypatch.setattr(function, "format_quota_markdown", lambda _: "Synthetic quota.")

    async def run():
        async with Client(function.mcp, mode=mode, cache=None) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools.keys() == CASES.keys()
            for name, (valid, invalid) in CASES.items():
                current = tools[name]
                assert current.annotations is not None
                Draft202012Validator.check_schema(current.input_schema)
                Draft202012Validator.check_schema(current.output_schema)
                input_validator = Draft202012Validator(current.input_schema)
                output_validator = Draft202012Validator(current.output_schema)
                input_validator.validate(valid)
                with pytest.raises(ValidationError):
                    input_validator.validate(invalid)
                previous = stubs[name].await_count
                bad_result = await client.call_tool(name, invalid)
                assert bad_result.is_error
                assert "fixture-private-marker" not in str(bad_result)
                assert stubs[name].await_count == previous
                result = await client.call_tool(name, valid)
                assert not result.is_error
                output_validator.validate(result.structured_content)
                with pytest.raises(ValidationError):
                    output_validator.validate({"result": [{"type": "text"}]})
                assert domain_result_from_call(result)["ok"] is (name != "get-operation")
                assert stubs[name].await_count == previous + 1

    asyncio.run(run())


class ExpiredAuth(RuntimeError):
    code = "AUTH_EXPIRED"


@pytest.mark.parametrize("raw,delay", [
    ("31", 31),
    (format_datetime(datetime.fromtimestamp(1_700_000_037, tz=timezone.utc), usegmt=True), 37),
    ("inf", None),
    ("0", None),
])
def test_rate_limit_recovery_is_structured_and_never_resubmits(monkeypatch, raw, delay):
    monkeypatch.setattr(resilience.time, "time", lambda: 1_700_000_000)
    error = RuntimeError("fixture-private-marker")
    error.response = SimpleNamespace(status_code=429, headers={"Retry-After": raw})
    fetch = AsyncMock(side_effect=error)
    monkeypatch.setattr(function, "fetch_initialized_quota_summary", fetch)
    monkeypatch.setattr("src.client_wrapper.get_gemini_client", lambda: SimpleNamespace(quotas={}))
    monkeypatch.setattr("src.infrastructure.account_identity.current_account_key", lambda: None)

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            listed = await client.list_tools()
            tool = next(tool for tool in listed.tools if tool.name == "check-quota")
            result = await client.call_tool(tool.name, {})
            Draft202012Validator(tool.output_schema).validate(result.structured_content)
            assert "fixture-private-marker" not in str(result)
            return domain_result_from_call(result)

    payload = asyncio.run(run())
    assert not payload["ok"]
    err = payload["error"]
    assert err["code"] == "RATE_LIMITED"
    assert payload["meta"]["details"].get("retry_after_seconds") == delay
    if delay is not None:
        assert payload["meta"]["details"]["reset_timestamp"] == 1_700_000_000 + delay
        assert payload["meta"]["details"]["quota_window"] == "http_retry_after"
    assert "tight loop" in err["suggested_action"] or "tight-loop" in err["suggested_action"]
    fetch.assert_awaited_once()


def test_expired_session_auth_keeps_original_handle_and_does_not_restart(monkeypatch):
    send = AsyncMock(side_effect=ExpiredAuth("fixture-private-marker"))
    start = AsyncMock()
    monkeypatch.setattr(assist._chat_service, "send_session_stream", send)
    monkeypatch.setattr(assist._chat_service, "start_session", start)

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            listed = await client.list_tools()
            tool = next(tool for tool in listed.tools if tool.name == "ask-gemini")
            result = await client.call_tool(tool.name, {"prompt": "synthetic", "session_id": "synthetic-session"})
            Draft202012Validator(tool.output_schema).validate(result.structured_content)
            assert "fixture-private-marker" not in str(result)
            return domain_result_from_call(result)

    result = asyncio.run(run())
    assert not result["ok"]
    assert result["error"]["code"] == "AUTH_EXPIRED"
    assert not result["error"]["retryable"]
    assert "same Gemini Web account" in result["error"]["suggested_action"]
    assert result["data"]["session_id"] == "synthetic-session"
    send.assert_awaited_once()
    start.assert_not_awaited()
