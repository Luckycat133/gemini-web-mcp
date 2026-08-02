"""P0.3 contract and cross-adapter regression tests."""

import asyncio
import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent

import src.client_wrapper as client_wrapper
import src.server as server
import src.skill_server as skill_server
import src.tools.chat as chat_tools
from src.adapters import attach_domain_result, domain_text
from src.domain import (
    DomainError,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    ResultMeta,
    result_from_exception,
)
from src.session_manager import SessionData, SessionOperationResult, SessionService


def _payload(content: TextContent) -> dict[str, Any]:
    assert content.meta is not None
    payload = content.meta["domain_result"]
    assert isinstance(payload, dict)
    return payload


def _run(awaitable):
    return asyncio.run(awaitable)


async def _call_primary(mcp: FastMCP, name: str, **kwargs: Any) -> list[TextContent]:
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


def test_success_contract_is_json_safe_and_explicit():
    warning = DomainWarning(
        code="FALLBACK_USED",
        message="A compatible fallback was selected.",
        suggested_action="Inspect the selected model if exact behavior matters.",
    )
    result = DomainResult.success(
        {"model": "flash"},
        warnings=(warning,),
        details={"operation": "chat"},
    )

    payload = result.to_dict()

    assert payload["ok"] is True
    assert payload["data"] == {"model": "flash"}
    assert payload["error"] is None
    assert payload["warnings"][0]["code"] == "FALLBACK_USED"
    assert payload["meta"]["operation_state"] == "completed"
    assert payload["meta"]["observed_at"].endswith("+00:00")
    assert payload["meta"]["verification_status"] == "not_applicable"
    assert re.fullmatch(r"req_[0-9a-f]{32}", payload["meta"]["request_id"])
    json.dumps(payload)


class _CodedError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@pytest.mark.parametrize(
    ("error", "expected_code", "retryable", "state"),
    [
        (ValueError("bad value"), "INVALID_ARGUMENT", False, "failed"),
        (_CodedError("NO_COOKIE", "PSID missing"), "AUTH_REQUIRED", False, "failed"),
        (_CodedError("INVALID_COOKIE", "cookie rejected"), "AUTH_EXPIRED", False, "failed"),
        (TimeoutError("slow secret upstream"), "TIMED_OUT", True, "timed_out"),
        (ConnectionError("socket secret"), "NETWORK_ERROR", True, "failed"),
        (
            _CodedError("MODEL_UNAVAILABLE", "model not available"),
            "CAPABILITY_UNAVAILABLE",
            False,
            "unavailable",
        ),
        (RuntimeError("upstream rejected private input"), "UPSTREAM_REJECTED", False, "failed"),
        (RuntimeError("response shape changed"), "UPSTREAM_CHANGED", False, "failed"),
        (RuntimeError("private internal details"), "INTERNAL_ERROR", False, "failed"),
        (asyncio.CancelledError(), "CANCELLED", True, "cancelled"),
    ],
)
def test_exception_taxonomy_is_stable_and_machine_readable(
    error,
    expected_code,
    retryable,
    state,
):
    result = result_from_exception(
        error,
        logger=logging.getLogger("tests.domain.taxonomy"),
        operation="test.operation",
    )
    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["error"]["code"] == expected_code
    assert payload["error"]["retryable"] is retryable
    assert payload["error"]["suggested_action"]
    assert payload["meta"]["operation_state"] == state
    assert payload["meta"]["verification_status"] == "exception_classified"
    assert payload["error"]["diagnostic_id"] == payload["meta"]["diagnostic_id"]
    assert re.fullmatch(r"diag_[0-9a-f]{32}", payload["meta"]["diagnostic_id"])
    json.dumps(payload)


def test_internal_exception_is_public_safe_but_raw_evidence_is_logged(caplog):
    caplog.set_level(logging.ERROR, logger="tests.domain.internal")
    secret = "raw-secret-token-123"

    result = result_from_exception(
        RuntimeError(secret),
        logger=logging.getLogger("tests.domain.internal"),
        operation="chat.generate",
    )
    payload_text = json.dumps(result.to_dict())

    assert secret not in payload_text
    assert result.meta.request_id in caplog.text
    assert result.meta.diagnostic_id in caplog.text
    assert secret in caplog.text


def test_contract_rejects_inconsistent_success_and_failure_states():
    with pytest.raises(ValueError, match="failure operation state"):
        DomainResult(
            ok=True,
            data=None,
            error=None,
            warnings=(),
            meta=ResultMeta.create(OperationState.FAILED),
        )

    with pytest.raises(ValueError, match="failure operation state"):
        DomainResult(
            ok=False,
            data=None,
            error=DomainError(
                code=DomainErrorCode.INTERNAL_ERROR,
                message="failed",
            ),
            warnings=(),
            meta=ResultMeta.create(OperationState.COMPLETED),
        )


def test_operation_state_taxonomy_matches_long_running_workflow_contract():
    assert {state.value for state in OperationState} == {
        "accepted",
        "queued",
        "running",
        "completed",
        "partial",
        "timed_out",
        "cancelled",
        "failed",
        "unavailable",
    }


def test_session_result_is_a_domain_result_and_excludes_runtime_objects():
    runtime_session = SimpleNamespace(cid="c_private", secret="do-not-serialize")
    state = SessionData(
        session=runtime_session,
        session_id="sess_public",
        model="flash",
    )
    result = SessionOperationResult.success(
        state,
        SimpleNamespace(text="upstream response object"),
    )

    payload = result.to_dict()

    assert isinstance(result, DomainResult)
    assert result.session is state
    assert result.response.text == "upstream response object"
    assert payload["data"]["state"]["session_id"] == "sess_public"
    assert "session" not in payload["data"]["state"]
    assert "response" not in payload["data"]
    assert "do-not-serialize" not in json.dumps(payload)


def test_mcp_adapter_preserves_text_and_embeds_serializable_result():
    result = DomainResult.success({"answer": 42})

    direct = domain_text(result, "legacy text", use_result_data=True)
    attached = attach_domain_result(
        [TextContent(type="text", text="existing text")],
        result,
        use_result_data=True,
    )

    assert direct[0].text == "legacy text"
    assert attached[0].text == "existing text"
    assert _payload(direct[0])["data"] == {"answer": 42}
    assert _payload(attached[0])["meta"]["operation_state"] == "completed"
    json.dumps(_payload(attached[0]))


def test_unknown_session_has_identical_domain_code_across_adapters(monkeypatch):
    service = SessionService()
    monkeypatch.setattr(client_wrapper, "_session_manager", service)
    primary = FastMCP("domain-parity")
    chat_tools.register_chat_tools(primary)

    async def run():
        primary_content = await _call_primary(
            primary,
            "gemini_send_message",
            session_id="sess_domain_missing",
            message="hello",
        )
        compact_content = await skill_server.session(
            "reset",
            session_id="sess_domain_missing",
        )
        return _payload(primary_content[0]), _payload(compact_content[0])

    primary_payload, compact_payload = _run(run())

    for payload in (primary_payload, compact_payload):
        assert payload["ok"] is False
        assert payload["error"]["code"] == "SESSION_NOT_FOUND"
        assert payload["error"]["retryable"] is False
        assert payload["error"]["suggested_action"]
        assert payload["meta"]["operation_state"] == "failed"


def test_invalid_image_is_typed_without_matching_legacy_emoji(monkeypatch):
    monkeypatch.setattr(
        chat_tools,
        "validate_image_paths",
        lambda _paths: (False, [], "image path is invalid"),
    )
    primary = FastMCP("invalid-argument")
    chat_tools.register_chat_tools(primary)

    content = _run(_call_primary(primary, "gemini_chat", message="hello"))
    payload = _payload(content[0])

    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["meta"]["operation_state"] == "failed"


def test_fastmcp_serializes_domain_result_under_meta_alias(monkeypatch):
    monkeypatch.setattr(
        chat_tools,
        "validate_image_paths",
        lambda _paths: (False, [], "invalid"),
    )
    primary = FastMCP("meta-alias")
    chat_tools.register_chat_tools(primary)

    _content, structured = _run(primary.call_tool("gemini_chat", {"message": "hello"}))
    payload = structured["result"][0]["_meta"]["domain_result"]

    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["meta"]["operation_state"] == "failed"


def test_chat_success_records_requested_effective_and_verification_metadata(monkeypatch):
    class SuccessClient:
        async def generate_content(self, **_kwargs):
            return SimpleNamespace(text="done")

    client = SuccessClient()

    async def noop_initialize():
        return client

    async def noop_cleanup(_client):
        return 0

    monkeypatch.setattr(chat_tools, "get_gemini_client", lambda: client)
    monkeypatch.setattr(chat_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(chat_tools, "cleanup_due_remote_chats", noop_cleanup)
    monkeypatch.setattr(chat_tools, "resolve_model_name", lambda _model: "web-flash")
    monkeypatch.setattr(
        chat_tools,
        "schedule_remote_chat_cleanup_from_response",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        chat_tools,
        "parse_response",
        lambda _response, _model: [TextContent(type="text", text="done")],
    )
    primary = FastMCP("success-metadata")
    chat_tools.register_chat_tools(primary)

    content = _run(
        _call_primary(
            primary,
            "gemini_chat",
            message="hello",
            model="flash",
        )
    )
    payload = _payload(content[0])

    assert payload["ok"] is True
    assert payload["meta"]["requested_backend"] == "flash"
    assert payload["meta"]["effective_backend"] == "web-flash"
    assert payload["meta"]["verification_status"] == "upstream_response_received"


def test_auth_failure_is_typed_at_primary_chat_boundary(monkeypatch):
    monkeypatch.setattr(
        chat_tools,
        "get_gemini_client",
        lambda: (_ for _ in ()).throw(_CodedError("NO_COOKIE", "PSID missing")),
    )
    primary = FastMCP("auth-failure")
    chat_tools.register_chat_tools(primary)

    content = _run(_call_primary(primary, "gemini_chat", message="hello"))
    payload = _payload(content[0])

    assert payload["error"]["code"] == "AUTH_REQUIRED"
    assert payload["error"]["retryable"] is False
    assert payload["meta"]["diagnostic_id"].startswith("diag_")


def test_timeout_has_identical_retryable_state_across_chat_adapters(monkeypatch):
    class TimeoutClient:
        async def generate_content(self, **_kwargs):
            raise TimeoutError("upstream took too long")

    client = TimeoutClient()

    async def noop_initialize():
        return client

    async def noop_cleanup(_client):
        return 0

    for module in (chat_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", noop_initialize)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", noop_cleanup)

    primary = FastMCP("timeout-parity")
    chat_tools.register_chat_tools(primary)

    async def run():
        primary_content = await _call_primary(primary, "gemini_chat", message="hello")
        compact_content = await skill_server.chat(message="hello")
        return _payload(primary_content[0]), _payload(compact_content[0])

    primary_payload, compact_payload = _run(run())

    for payload in (primary_payload, compact_payload):
        assert payload["error"]["code"] == "TIMED_OUT"
        assert payload["error"]["retryable"] is True
        assert payload["meta"]["operation_state"] == "timed_out"


def test_client_reset_exposes_typed_completed_state(monkeypatch):
    async def noop_reset():
        return None

    monkeypatch.setattr(server, "reset_client_async", noop_reset)

    content = _run(server.mcp.call_tool("gemini_reset", {}))[0]
    payload = _payload(content[0])

    assert content[0].text == "✅ 客户端已重置"
    assert payload["ok"] is True
    assert payload["data"] == {"client_state": "reset"}
    assert payload["meta"]["operation_state"] == "completed"
    assert payload["meta"]["verification_status"] == "local_state_reset"


def test_chat_tool_names_remain_registered():
    primary = FastMCP("tool-name-compatibility")
    chat_tools.register_chat_tools(primary)

    tools = _run(primary.list_tools())
    names = {tool.name for tool in tools}

    assert {
        "gemini_chat",
        "gemini_start_chat",
        "gemini_send_message",
        "gemini_reset_session",
        "gemini_list_sessions",
        "gemini_chat_stream",
        "gemini_send_message_stream",
    } <= names
