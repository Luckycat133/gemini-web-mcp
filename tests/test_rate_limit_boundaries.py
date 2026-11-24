"""Regression tests for ClientRateLimited error mapping and worker recovery throttling."""

from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp import Client

from src import client_manager, client_wrapper
from src.adapters.mcp_sdk import MCPServer
from src.client_manager import ClientRateLimited
from src.infrastructure.operation_store import OperationStore
from src.onboarding import domain_result_from_call
from src.services import background
from src.services.managed_skills import ManagedSkillDependencies, ManagedSkillService
from src.services.notebook_admin import NotebookAdminDependencies, NotebookAdminService
from src.services.preferences import PreferencesDependencies, PreferencesService
from src.tools.manage import _register_gem_tools


def test_notebook_admin_maps_rate_limited_without_upsteam_changed_mask() -> None:
    initializer = AsyncMock(side_effect=ClientRateLimited())
    request, projects = AsyncMock(), AsyncMock()
    service = NotebookAdminService(NotebookAdminDependencies(
        lambda: object(), initializer, request, projects, lambda: "acct_1",
    ))
    for action in ("get", "delete"):
        result = asyncio.run(service.run(action, notebook_id="fixture"))
        assert not result.ok
        assert result.error is not None
        assert result.error.code.value == "RATE_LIMITED"
        assert result.error.retryable is True
    assert request.await_count == projects.await_count == 0


def test_managed_skills_maps_rate_limited_on_read_and_mutate() -> None:
    initializer = AsyncMock(side_effect=ClientRateLimited())
    read, upload, mutate = AsyncMock(), AsyncMock(), AsyncMock()
    service = ManagedSkillService(ManagedSkillDependencies(
        lambda: object(), initializer, read, lambda: "acct_1", upload, mutate,
    ))
    for result in (asyncio.run(service.read()), asyncio.run(service.mutate("delete", skill_id="fixture"))):
        assert not result.ok
        assert result.error is not None
        assert result.error.code.value == "RATE_LIMITED"
        assert result.error.retryable is True
    assert read.await_count == upload.await_count == mutate.await_count == 0


def test_preferences_service_maps_rate_limited_even_without_account_identity() -> None:
    initializer = AsyncMock(side_effect=ClientRateLimited())
    service = PreferencesService(PreferencesDependencies(
        lambda: object(), initializer, AsyncMock(), AsyncMock(), AsyncMock(), lambda: None,
    ))
    for result in (asyncio.run(service.get_browser()), asyncio.run(service.set_browser(browser="automatic"))):
        assert not result.ok
        assert result.error is not None
        assert result.error.code.value == "RATE_LIMITED"
        assert result.error.retryable is True


def test_submit_media_maps_rate_limited_from_browser_cookie_check(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "ops.sqlite3"))
    monkeypatch.delenv("GEMINI_PSID", raising=False)

    def raise_rate_limit() -> None:
        raise ClientRateLimited()

    monkeypatch.setattr(client_wrapper, "ensure_web_cookie", raise_rate_limit)
    dispatch = AsyncMock()
    monkeypatch.setattr(background, "_dispatch", dispatch)
    handle = "op_" + "e" * 32
    result = asyncio.run(background.submit_media(
        {
            "prompt": "synthetic-prompt",
            "filename": "out",
            "output_dir": str(tmp_path),
            "media_type": "video",
        },
        operation="gemini_generate_video",
        operation_id=handle,
    ))
    assert not result.ok
    assert result.error is not None
    assert result.error.code.value == "RATE_LIMITED"
    dispatch.assert_not_awaited()
    assert OperationStore().get(handle) is None


def test_gemini_manage_gems_returns_structured_rate_limited(monkeypatch) -> None:
    from src.tools import manage as manage_tools

    monkeypatch.setattr(manage_tools, "current_account_key", lambda: "acct_1")
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: object())
    monkeypatch.setattr(manage_tools, "initialize_client", AsyncMock(side_effect=ClientRateLimited()))

    server = MCPServer("test-manage-gems")
    _register_gem_tools(server, enabled_tool_names={"gemini_manage_gems"})

    async def call_gems():
        async with Client(server) as client:
            return await client.call_tool("gemini_manage_gems", {"action": "list"})

    res = asyncio.run(call_gems())
    dr = domain_result_from_call(res)
    assert isinstance(dr, dict)
    assert dr["ok"] is False
    assert dr["error"]["code"] == "RATE_LIMITED"
    assert dr["error"]["retryable"] is True


def test_maintain_background_operations_throttles_to_one_worker_and_skips_when_rate_limited(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("GEMINI_PSID", "psid_test")
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "ops.sqlite3"))
    store = OperationStore()
    for idx in range(3):
        op_id = f"op_{idx}" + "f" * 29
        store.create_or_get(op_id, "gemini_generate_music", media_type="music", output_dir=str(tmp_path), filename=f"f{idx}")
        store.claim_worker(op_id, "old")
        store.checkpoint_media(op_id, worker_token="old", chat_id=f"c_{idx}", response_id=f"r_{idx}", candidate_id=f"rc_{idx}")
        store.finish_worker(op_id, worker_token="old", state="partial", phase="media_artifact_not_observed", verification_status="not_observed")
        store.retire_worker(op_id, "old")
    with store._connect() as db:
        db.execute("UPDATE operations SET updated_at=0, heartbeat_at=0")
    store.clock = lambda: time.time() + 65

    monkeypatch.setattr(background, "OperationStore", lambda: store)
    dispatched: list[str] = []

    async def fake_dispatch(_store, rec, kind, request=None):
        dispatched.append(f"{kind}:{rec.operation_id}")
        return background._accepted(rec)

    monkeypatch.setattr(background, "_dispatch", fake_dispatch)

    # First tick: rate-limited manager skips maintenance completely.
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(is_rate_limited=lambda: True))
    asyncio.run(background.maintain_background_operations())
    assert dispatched == []

    # Next tick when not rate-limited: dispatches at most ONE worker even with 3 recoverable rows.
    monkeypatch.setattr(client_wrapper, "_client_manager", SimpleNamespace(is_rate_limited=lambda: False))
    asyncio.run(background.maintain_background_operations())
    assert len(dispatched) == 1

    # While one recoverable row already has an active worker, dispatches zero more.
    first_id = dispatched[0].split(":", 1)[1]
    assert store.claim_worker(first_id, "active_tok")
    store.bind_worker(first_id, "active_tok", os.getpid())
    asyncio.run(background.maintain_background_operations())
    assert len(dispatched) == 1
    assert client_manager.ClientRateLimited.code == "RATE_LIMITED"
    assert json.dumps({"ok": True})
