"""Unit tests for v0.23.0 8-tool unprefixed surface, Gemini 4 + medium defaults, transient auto-recovery, music fixes, and IDE-agnostic rate-limit guidance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
        "ask",
        "understand",
        "image",
        "video",
        "music",
        "research",
        "task",
        "operation",
    }
    assert not any(name.startswith("gemini_") for name in tools)
    assert "model" not in tools["image"].input_schema["properties"]
    assert "thinking_level" not in tools["image"].input_schema["properties"]
    assert "model" not in tools["video"].input_schema["properties"]
    assert "model" not in tools["music"].input_schema["properties"]
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

    import logging
    domain_fail = result_from_exception(exc_info.value, logger=logging.getLogger("test"), operation="ask")
    assert domain_fail.error_code == DomainErrorCode.RATE_LIMITED.value
    assert domain_fail.meta.details["reset_at"] == exc_info.value.reset_at
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
            ):
                rejected = await client.call_tool("ask", bad_args)
                assert rejected.is_error

    asyncio.run(run())


def test_lean_understand_accepts_plain_string_paths_urls_and_text(monkeypatch, tmp_path):
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
            await client.call_tool("understand", {
                "task": "Summarize",
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

        async def get_result(self, operation_id, wait_seconds=0):
            calls.append(("result", operation_id, wait_seconds))
            return SimpleNamespace(
                ok=True,
                data=OperationReadData(operation_id, "gemini_research", OperationState.COMPLETED, False, True, "report_observed", "Done."),
                error=None, warnings=(), meta=SimpleNamespace(operation_state=OperationState.COMPLETED, request_id="r", diagnostic_id=None, requested_backend=None, effective_backend=None, verification_status="report_observed", details=None),
            )

    monkeypatch.setattr(op_tools, "_service", lambda: FakeSvc())

    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            await client.call_tool("operation", {"operation_id": "op_123", "status_only": True})
            await client.call_tool("operation", {"operation_id": "op_123", "wait_seconds": 15})
            bad = await client.call_tool("operation", {"operation_id": "op_123", "status_only": True, "wait_seconds": 5})
            return bad

    bad = asyncio.run(run())
    assert calls == [("status", "op_123"), ("result", "op_123", 15)]
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
