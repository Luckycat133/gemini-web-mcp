"""Unit tests for v0.22.0 10-tool split, Gemini 4 model defaults, transient auto-recovery, and rate-limit reset guidance."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import AsyncMock

from gemini_webapi.constants import AccountStatus
from gemini_webapi.types import AvailableModel
from mcp import Client
import pytest

from src.client_manager import ClientManager, ClientRateLimited
from src.domain import (
    CleanupObservation,
    CleanupState,
    DomainErrorCode,
    OperationState,
    result_from_exception,
)
from src.domain.operations import OperationReadData
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
from src.surfaces import function
from src.thinking_client import ThinkingLevelGeminiClient


def test_split_function_catalog_has_10_single_purpose_tools():
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}

    tools = asyncio.run(run())
    assert set(tools) == {
        "gemini_ask",
        "gemini_understand",
        "gemini_generate_image",
        "gemini_edit_image",
        "gemini_generate_video",
        "gemini_generate_music",
        "gemini_research",
        "gemini_run_task",
        "gemini_get_operation_status",
        "gemini_get_operation_result",
    }
    assert "gemini_create_media" not in tools
    assert "inputs" not in tools["gemini_ask"].input_schema["properties"]
    assert "kind" not in tools["gemini_generate_image"].input_schema["properties"]
    assert "music_genre" not in tools["gemini_generate_video"].input_schema["properties"]
    assert "duration_seconds" not in tools["gemini_generate_music"].input_schema["properties"]


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
    assert "schedule" in action.lower() and "/schedule" in action

    # ClientManager cooldown error carries exact wall-clock reset details
    manager = ClientManager()
    manager._remember_rate_limit()
    with pytest.raises(ClientRateLimited) as exc_info:
        manager._check_rate_limit()
    assert exc_info.value.reset_at is not None
    assert exc_info.value.retry_after_seconds is not None

    import logging
    domain_fail = result_from_exception(exc_info.value, logger=logging.getLogger("test"), operation="gemini_ask")
    assert domain_fail.error_code == DomainErrorCode.RATE_LIMITED.value
    assert domain_fail.meta.details["reset_at"] == exc_info.value.reset_at
    assert "schedule" in (domain_fail.error.suggested_action or "").lower()

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
    assert "schedule" in (op_fail.error.suggested_action or "").lower()
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
