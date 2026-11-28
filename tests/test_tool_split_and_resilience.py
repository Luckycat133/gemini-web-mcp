"""Unit tests for v0.23.0 8-tool unprefixed surface, Gemini 4 + medium defaults, transient auto-recovery, music fixes, and IDE-agnostic rate-limit guidance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import json
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock

from gemini_webapi.constants import AccountStatus
from gemini_webapi.types import AvailableModel
from mcp import Client
import pytest

from src.client_manager import ClientManager, ClientRateLimited
from src.constants import is_music_text_blueprint_reply, is_music_tool_unavailable_reply
from src.domain import (
    CleanupObservation,
    CleanupState,
    DomainErrorCode,
    DomainResult,
    OperationState,
    result_from_exception,
)
from src.domain.operations import OperationReadData
from src.infrastructure.media_companions import MediaCompanion, MediaCompanionCatalog, MediaCompanionOption
from src.infrastructure.model_policy_store import ModelPolicyStore
from src.services.chat import ChatRequest, ChatService, ChatServiceDependencies
from src.services.operations import _next_wait_poll_seconds, _operation_read_result
from src.services.quota import fetch_initialized_quota_summary, fetch_quota_summary
from src.services.resilience import (
    extract_quota_reset_from_quotas,
    format_rate_limit_guidance,
    is_transient_error,
    persist_client_quota_snapshot,
    resolve_rate_limit_reset,
)
from src.surfaces import assist, function
from src.tools import operations as op_tools
from src.thinking_client import ThinkingLevelGeminiClient, WebRequestOptions


def test_split_function_catalog_has_8_unprefixed_lean_tools_and_lower_token_budget():
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    tools = asyncio.run(run())
    assert set(tools) == {
        "ask-gemini",
        "generate-image",
        "generate-video",
        "generate-music",
        "run-research",
        "run-task",
        "get-operation",
        "check-quota",
    }
    assert not any(name.startswith("gemini_") for name in tools)
    assert all("-" in name for name in tools)
    assert "understand" not in tools
    assert "inputs" in tools["ask-gemini"].input_schema["properties"]
    assert "learning_mode" not in tools["ask-gemini"].input_schema["properties"]
    assert "gem_id" not in tools["ask-gemini"].input_schema["properties"]
    assert all("ctx" not in tool.input_schema["properties"] for tool in tools.values())
    assert "model" not in tools["generate-image"].input_schema["properties"]
    assert "thinking_level" not in tools["generate-image"].input_schema["properties"]
    assert "operation_id" not in tools["generate-image"].input_schema["properties"]
    assert "model" not in tools["generate-video"].input_schema["properties"]
    assert "model" not in tools["generate-music"].input_schema["properties"]
    assert "Nano Banana" not in (function.mcp.instructions or "")
    assert "Lyria" not in (function.mcp.instructions or "")
    assert "Gemini Omni" not in (function.mcp.instructions or "")
    total_chars = len(function.mcp.instructions or "") + sum(
        len(t.name) + len(t.description or "") + len(json.dumps(t.input_schema, ensure_ascii=False))
        for t in tools.values()
    )
    assert total_chars < 10_500


def test_transient_error_classifier_excludes_non_retryable_or_rate_limit_errors():
    assert is_transient_error(ConnectionError("connection reset by peer")) is True
    assert is_transient_error(TimeoutError("timed out")) is True
    http_503 = RuntimeError("HTTP 503 service unavailable")
    http_503.status_code = 503  # type: ignore[attr-defined]
    assert is_transient_error(http_503) is True

    http_429 = RuntimeError("HTTP 429 rate limited")
    http_429.status_code = 429  # type: ignore[attr-defined]
    assert is_transient_error(http_429) is False
    assert is_transient_error(ClientRateLimited(retry_after_seconds=30)) is False
    assert is_transient_error(ValueError("bad argument")) is False
    assert is_transient_error(PermissionError("auth required")) is False


def test_chat_service_auto_recovers_transient_error_and_generic_failure_before_surfacing(tmp_path):
    events: list[str] = []
    attempts = 0
    response_ok = SimpleNamespace(
        text="Clean answer after transient recovery",
        metadata=["c_rec", "r_rec"],
        rcid="rc_rec",
        images=[],
        videos=[],
        media=[],
    )

    class FlakyClient:
        def create_cleanup_chat(self):
            return SimpleNamespace(cid="", metadata=[])

        async def generate_content(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            events.append(f"attempt:{attempts}")
            if attempts == 1:
                raise ConnectionError("connection reset before remote CID")
            return response_ok

    service = ChatService(
        ChatServiceDependencies(
            client_provider=lambda: FlakyClient(),
            client_initializer=AsyncMock(),
            cleanup_due_remote_chats=AsyncMock(return_value=0),
            create_session=AsyncMock(),
            lookup_session=AsyncMock(),
            send_session_message=AsyncMock(),
            send_session_message_stream=AsyncMock(),
            schedule_response_cleanup=lambda *a, **kw: CleanupObservation(
                state=CleanupState.COMPLETED, upstream_chat_id="c_rec", source="test"
            ),
            schedule_chat_cleanup=lambda cid, **kw: CleanupObservation(
                state=CleanupState.COMPLETED, upstream_chat_id=cid, source="test"
            ),
            normalize_model=lambda value: value,
            resolve_model=lambda value: value,
            account_key_provider=lambda: "acct_test",
        )
    )
    res = asyncio.run(service.generate(ChatRequest(message="Test transient retry")))
    assert res.ok
    assert res.data is not None and res.data.response.text == "Clean answer after transient recovery"
    assert events == ["attempt:1", "attempt:2"]


def test_quota_reset_extraction_persistence_and_scheduled_retry_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-rate-limit-account")
    db_path = tmp_path / "operations.sqlite3"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(db_path))

    future_ts = time.time() + 1800
    future_iso = datetime.fromtimestamp(future_ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    client = ThinkingLevelGeminiClient()
    client._quotas = {
        "usage_info": {
            "current_5h": {"remaining_credits": 0, "reset_at": future_iso},
            "weekly": {"remaining_credits": 900000, "reset_at": "2026-12-01T22:53:51+08:00"},
        }
    }
    snap = persist_client_quota_snapshot(client, account_key="acct_quota")
    assert snap is not None
    assert snap["quota_window"] == "current_5h"

    stored = ModelPolicyStore(db_path).get_quota_snapshot("acct_quota")
    assert stored is not None and abs(stored["reset_timestamp"] - future_ts) < 5

    extracted = extract_quota_reset_from_quotas(client._quotas)
    assert extracted is not None and extracted["quota_window"] == "current_5h"

    info = resolve_rate_limit_reset(account_key="acct_quota")
    assert info["quota_window"] == "current_5h"
    assert info["retry_after_seconds"] >= 1700

    msg, action = format_rate_limit_guidance(info)
    assert info["reset_at"] in msg
    assert "/schedule" not in action
    assert "DurationSeconds" not in action
    assert "host environment" in action and info["reset_at"] in action

    # ClientManager cooldown error carries exact wall-clock reset details
    manager = ClientManager()
    manager._remember_rate_limit()
    with pytest.raises(ClientRateLimited) as exc_info:
        manager._check_rate_limit()
    assert exc_info.value.reset_at is not None
    assert exc_info.value.retry_after_seconds is not None

    domain_fail = result_from_exception(exc_info.value, logger=logging.getLogger("test"), operation="ask")
    assert domain_fail.error_code == DomainErrorCode.RATE_LIMITED.value
    assert domain_fail.meta.details["reset_at"] == exc_info.value.reset_at
    assert domain_fail.meta.details["retry_after_source"] == "browser_probe_cooldown"
    assert domain_fail.error is not None and "local cooldown" in domain_fail.error.message
    assert "quota" not in domain_fail.error.message.lower()
    assert "host environment" in (domain_fail.error.suggested_action or "").lower()
    assert "/schedule" not in (domain_fail.error.suggested_action or "")

    # Worker UPSTREAM_LIMIT_EXCEEDED read-back uses persisted snapshot too
    monkeypatch.setattr("src.infrastructure.account_identity.current_account_key", lambda: "acct_quota")
    op_fail = _operation_read_result(
        OperationReadData(
            operation_id="op_" + "a" * 32,
            operation="gemini_generate_video",
            state=OperationState.FAILED,
            continuation_possible=False,
            report_available=False,
            verification_status="upstream_limit_message_observed",
            error_code="UPSTREAM_LIMIT_EXCEEDED",
        )
    )
    assert op_fail.error is not None and op_fail.error.code == DomainErrorCode.RATE_LIMITED
    assert "host environment" in (op_fail.error.suggested_action or "").lower()
    assert "/schedule" not in (op_fail.error.suggested_action or "")
    assert op_fail.meta.details["reset_at"] == stored["reset_at"]
    assert op_fail.meta.details["retry_after_source"] == "stored_quota_snapshot"
    assert "quota snapshot" in op_fail.error.message


@pytest.mark.parametrize("remaining", [33360, None, False])
def test_quota_window_with_credits_or_unknown_balance_does_not_explain_429(monkeypatch, tmp_path, remaining):
    db = tmp_path / "operations.sqlite3"
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(db))
    monkeypatch.setattr("src.infrastructure.account_identity.current_account_key", lambda: "acct_test")
    future_ts = time.time() + 1800
    future_iso = datetime.fromtimestamp(future_ts, tz=timezone.utc).isoformat()
    ModelPolicyStore(db).record_quota_snapshot(
        "acct_test", quota_window="current_5h", reset_at=future_iso,
        reset_timestamp=future_ts, remaining_credits=0,
    )
    client = SimpleNamespace(quotas={
        "usage_info": {"current_5h": {"remaining_credits": remaining, "reset_at": future_iso}},
        "None-11": {"reset_time": future_ts, "remaining": 0},
    })
    monkeypatch.setattr("src.client_wrapper.get_gemini_client", lambda: client)
    error = RuntimeError("HTTP 429")
    result = result_from_exception(error, logger=logging.getLogger("test"), operation="ask")
    assert result.error is not None and result.error.code == DomainErrorCode.RATE_LIMITED
    assert "reset_at" not in result.meta.details and "retry_after_seconds" not in result.meta.details
    assert "unconfirmed" in result.error.message
    assert "off-peak" in (result.error.suggested_action or "")
    assert "confirmed failed request only once" in (result.error.suggested_action or "")


@pytest.mark.parametrize("delays,expected_init_calls,expect_success", [
    ([30], 2, True),
    ([30, 30], 2, False),
    ([31], 1, False),
])
def test_quota_initialization_retries_only_one_bounded_local_cooldown(monkeypatch, delays, expected_init_calls, expect_success):
    initialize = AsyncMock(side_effect=[
        *(ClientRateLimited(retry_after_seconds=delay, quota_window="browser_probe_cooldown") for delay in delays),
        None,
    ])
    sleep = AsyncMock()
    monkeypatch.setattr("src.services.quota.asyncio.sleep", sleep)
    client = SimpleNamespace(usage_info={"current_5h": {"remaining_credits": 9}})
    result = asyncio.run(fetch_initialized_quota_summary(lambda: client, initialize))
    assert result.ok is expect_success
    assert initialize.await_count == expected_init_calls
    if expected_init_calls == 2:
        sleep.assert_awaited_once_with(30)
    else:
        sleep.assert_not_awaited()


def test_failed_fresh_quota_lookup_labels_cached_balance():
    client = SimpleNamespace(
        _batch_execute=AsyncMock(return_value=SimpleNamespace(text="empty")),
        _parse_rpc_results=lambda *_: [],
        usage_info={"current_5h": {"remaining_credits": 9}},
    )
    result = asyncio.run(fetch_quota_summary(client))
    assert result.ok and result.meta.verification_status == "quota_cached"


def test_adaptive_wait_poll_interval():
    assert _next_wait_poll_seconds(0.0) == 0.5
    assert _next_wait_poll_seconds(9.9) == 0.5
    assert _next_wait_poll_seconds(15.0) == 1.0
    assert _next_wait_poll_seconds(35.0) == 2.0


def test_no_sensitive_word_list_pre_interception():
    """Verify SKILL policy avoids sensitive-word blocklists and pro maps to 4 Neon."""
    skill_text = (Path(__file__).resolve().parents[1] / ".agents/skills/gemini-web-mcp/SKILL.md").read_text(encoding="utf-8")
    assert "Do **not** pre-screen or block prompts locally" in skill_text
    assert "UPSTREAM_REJECTED" in skill_text
    # Ensure pro resolves to 4 Neon on Gemini 4 client
    c = ThinkingLevelGeminiClient()
    c._running = True
    c.account_status = AccountStatus.AVAILABLE
    c._usage_info = {"tier": {"id": 3, "label": "ULTRA"}}
    c._model_registry = {
        "n": AvailableModel(model_id="n", model_name="gemini-neon", display_name="4 Neon", aliases=["neon"], description="", capacity=2)
    }
    assert c._resolve_policy_model("pro").model_name == "gemini-neon"


def test_lean_ask_rejects_legacy_pro_flash_and_standard_and_defaults_to_hydrogen_medium():
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            for bad_args in (
                {"prompt": "hi", "model": "pro"},
                {"prompt": "hi", "model": "flash"},
                {"prompt": "hi", "thinking_level": "standard"},
                {"prompt": "hi", "learning_mode": "quiz"},
                {"prompt": "hi", "gem_id": "gem_1"},
            ):
                rejected = await client.call_tool("ask-gemini", bad_args)
                assert rejected.is_error

    asyncio.run(run())


def test_lean_ask_gemini_inputs_accepts_plain_string_paths_urls_and_text(monkeypatch, tmp_path):
    img = tmp_path / "pic.jpg"
    img.write_bytes(b"jpg-bytes")
    captured = []

    class FakeUnderstandService:
        async def understand(self, req):
            captured.append(req)
            return DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "stop-after-capture")

    monkeypatch.setattr(assist, "_understand_service", FakeUnderstandService())

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            await client.call_tool("ask-gemini", {
                "prompt": "Summarize",
                "inputs": [str(img), "https://example.com/page", "Plain inline note"],
            })

    asyncio.run(run())

    assert len(captured) == 1
    req = captured[0]
    assert req.model == "hydrogen" and req.thinking_level == "medium"
    assert [i.kind.value for i in req.inputs] == ["image", "url", "text"]
    assert req.inputs[0].path == str(img)
    assert req.inputs[1].url == "https://example.com/page"
    assert req.inputs[2].text == "Plain inline note"


def test_lean_operation_handles_status_only_and_rejects_wait_combination(monkeypatch):
    calls = []

    class FakeSvc:
        async def get_status(self, operation_id):
            calls.append(("status", operation_id))
            return SimpleNamespace(
                ok=True,
                data=OperationReadData(operation_id, "gemini_research", OperationState.RUNNING, True, False, "worker_active"),
                error=None, warnings=(), meta=SimpleNamespace(operation_state=OperationState.RUNNING, request_id="r", diagnostic_id=None, requested_backend=None, effective_backend=None, verification_status="worker_active", details=None),
            )

        async def get_result(self, operation_id, wait_seconds=0, on_progress=None):
            calls.append(("result", operation_id, wait_seconds, callable(on_progress)))
            if on_progress is not None:
                await on_progress(0.5, float(wait_seconds), "waiting")
            return SimpleNamespace(
                ok=True,
                data=OperationReadData(operation_id, "gemini_research", OperationState.COMPLETED, False, True, "report_observed", "Done."),
                error=None, warnings=(), meta=SimpleNamespace(operation_state=OperationState.COMPLETED, request_id="r", diagnostic_id=None, requested_backend=None, effective_backend=None, verification_status="report_observed", details=None),
            )

    monkeypatch.setattr(op_tools, "_service", lambda: FakeSvc())

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            await client.call_tool("get-operation", {"operation_id": "op_123", "status_only": True})
            await client.call_tool("get-operation", {"operation_id": "op_123", "wait_seconds": 15})
            bad = await client.call_tool("get-operation", {"operation_id": "op_123", "status_only": True, "wait_seconds": 5})
            return bad

    bad = asyncio.run(run())
    assert calls == [("status", "op_123"), ("result", "op_123", 15, True)]
    assert "INVALID_ARGUMENT" in bad.content[0].text


def test_music_refusal_blueprint_detection_and_mode21_companion_options():
    assert is_music_tool_unavailable_reply(
        "I'm unable to generate audio tracks directly in this interface because I don't have access to a music generation tool right now."
    )
    assert is_music_text_blueprint_reply("### Suggested Track Blueprint (78 BPM, 4/4 Time, F Major)")

    client = ThinkingLevelGeminiClient()
    client._mcp_media_companions = MediaCompanionCatalog(
        by_mode={
            21: (
                MediaCompanion(
                    id=16, mode=21, title="时长", selection_mode=1, available=True,
                    options=(
                        MediaCompanionOption(id=42, title="短版", is_default=False, available=True),
                        MediaCompanionOption(id=43, title="标准", is_default=True, available=True),
                    ),
                ),
                MediaCompanion(
                    id=17, mode=21, title="人声", selection_mode=1, available=True,
                    options=(
                        MediaCompanionOption(id=69, title="自定义", is_default=True, available=True),
                        MediaCompanionOption(id=26, title="纯音乐", is_default=False, available=True),
                    ),
                ),
                MediaCompanion(
                    id=18, mode=21, title="流派", selection_mode=1, available=True,
                    options=(
                        MediaCompanionOption(id=41, title="自定义", is_default=True, available=True),
                        MediaCompanionOption(id=68, title="氛围音乐", is_default=False, available=True),
                    ),
                ),
            ),
        },
        capabilities=frozenset({230}),
    )
    assert client._media_companion_options(
        WebRequestOptions(music_generation=True, music_duration="short", music_vocals="instrumental", music_genre="ambient")
    ) == (42, 26, 68)


def test_check_quota_tool_formats_exact_5h_and_weekly_credits_and_reset_times(monkeypatch):
    from gemini_webapi.constants import GRPC

    rpc_calls = []

    class FakeQuotaClient:
        async def _batch_execute(self, reqs, *, source_path="/"):
            rpc_calls.append(([(r.rpcid, r.payload) for r in reqs], source_path))
            return SimpleNamespace(text="mock-rpc-body")

        def _parse_rpc_results(self, _text, rpcid):
            assert rpcid == GRPC.GET_USAGE_INFO
            return [[
                3,
                [
                    [46135, 0.04, 1, [[1795762431, 0]], None, None, 48000],
                    [891892, 0.07831884, 2, [[1796136831, 0]], None, None, 967680],
                ],
                False,
            ]]

    monkeypatch.setattr(function, "initialize_client", AsyncMock())
    monkeypatch.setattr(function, "get_gemini_client", lambda: FakeQuotaClient())

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("check-quota", {})

    res = asyncio.run(run())
    assert rpc_calls == [([(GRPC.GET_USAGE_INFO, "[]")], "/usage")]
    text = res.content[0].text
    assert "ULTRA" in text
    assert "46,135 / 48,000 credits remaining" in text
    assert "891,892 / 967,680 credits remaining" in text
    assert "4% used" in text
    assert "7.8% used" in text


def test_task_auto_approval_defaults_on_and_honors_explicit_opt_out(monkeypatch):
    from src.services.task_inputs import task_auto_approve_enabled
    from src.services.web_task_approvals import web_task_approvals_enabled

    monkeypatch.delenv("GEMINI_TASK_AUTO_APPROVE", raising=False)
    monkeypatch.delenv("GEMINI_TASK_WEB_APPROVALS", raising=False)
    assert task_auto_approve_enabled() is True
    assert web_task_approvals_enabled() is True

    monkeypatch.setenv("GEMINI_TASK_AUTO_APPROVE", "false")
    monkeypatch.setenv("GEMINI_TASK_WEB_APPROVALS", "0")
    assert task_auto_approve_enabled() is False
    assert web_task_approvals_enabled() is False


def test_select_auto_approve_option_handles_generic_cards_and_keeps_browser_guard():
    from src.infrastructure.task_confirmation import select_auto_approve_option

    generic_turn = {
        "input_options": [
            {"input_option_id": "opt_deny", "label": "取消", "prompt": "deny"},
            {"input_option_id": "opt_allow", "label": "继续执行", "prompt": "allow"},
        ],
        "input_card": {
            "requires_option_id": True,
            "replies": [
                {"input_option_id": "opt_deny", "action_path": [8, 0], "prompt": "deny"},
                {"input_option_id": "opt_allow", "action_path": [7, 0], "prompt": "allow"},
            ],
        },
    }
    assert select_auto_approve_option(generic_turn) == {
        "input_option_id": "opt_allow",
        "prompt": "allow",
    }

    tampered_browser_turn = {
        "input_options": [{"input_option_id": "opt_1", "label": "始终允许"}],
        "input_card": {
            "requires_option_id": True,
            "browser_permission_card": True,
            "replies": [{"input_option_id": "opt_1", "prompt": "always"}],
        },
    }
    assert select_auto_approve_option(tampered_browser_turn) is None


def test_operation_service_get_result_invokes_on_progress_callback(tmp_path, monkeypatch):
    from src.infrastructure.operation_store import OperationStore
    from src.services.operations import OperationService

    monkeypatch.setenv("GEMINI_PSID", "test-psid")

    ticks = iter([1000.0, 1000.1, 1000.2, 1000.3, 1000.4])
    store = OperationStore(tmp_path / "ops.sqlite3", clock=lambda: next(ticks, 1000.4))
    store.create("op_prog_1", "gemini_research")
    svc = OperationService(
        store,
        client_provider=lambda: None,
        client_initializer=AsyncMock(),
        finalize_chat_cleanup=AsyncMock(),
    )

    reads = 0

    async def fake_read(op_id: str, *, include_report: bool):
        nonlocal reads
        reads += 1
        assert op_id == "op_prog_1" and include_report is True
        return DomainResult.success(
            OperationReadData(op_id, "gemini_research", OperationState.COMPLETED, False, True, "report_observed", "Done."),
            operation_state=OperationState.COMPLETED,
            verification_status="report_observed",
        )

    svc._read = fake_read  # type: ignore[method-assign]
    progress_events: list[tuple[float, float, str]] = []

    async def on_progress(progress: float, total: float, message: str) -> None:
        progress_events.append((progress, total, message))

    res = asyncio.run(svc.get_result("op_prog_1", wait_seconds=1, on_progress=on_progress))
    assert res.ok and res.data is not None and res.data.report == "Done."
    assert any("finished" in msg for _, _, msg in progress_events)


def test_lean_generate_music_uses_standard_native_thinking_level(monkeypatch):
    captured: list[dict] = []

    async def fake_submit(**kwargs):
        captured.append(kwargs)
        return function.domain_text(
            DomainResult.failure(DomainErrorCode.INTERNAL_ERROR, "stop-after-capture"),
            "stop-after-capture",
        )

    monkeypatch.setattr(function, "_submit", fake_submit)

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool(
                "generate-music",
                {"prompt": "Calm guitar melody", "duration": "short", "vocals": "instrumental"},
            )

    asyncio.run(run())
    assert len(captured) == 1
    assert captured[0]["thinking_level"] == "standard"
    assert captured[0]["music_duration"] == "short"
    assert captured[0]["music_vocals"] == "instrumental"
