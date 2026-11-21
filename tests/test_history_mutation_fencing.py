import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from gemini_webapi.constants import AccountStatus
from gemini_webapi.exceptions import APIError

from src.services.history import delete_chat_result
from src.thinking_client import ThinkingLevelGeminiClient


def _client(post):
    client = ThinkingLevelGeminiClient(secure_1psid="synthetic")
    client._running = True
    client.account_status = AccountStatus.AVAILABLE
    client.client = SimpleNamespace(post=post)
    client.close = AsyncMock()
    return client


def test_sdk_delete_failure_has_one_remote_dispatch(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account")
    post = AsyncMock(return_value=SimpleNamespace(status_code=500))
    with pytest.raises(APIError):
        asyncio.run(delete_chat_result(_client(post), "c_fixture"))
    assert post.await_count == 1


def test_account_switch_after_delete_stops_sdk_followup_and_scrubs_result(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    async def post(*_args, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-account-b")
        return SimpleNamespace(status_code=200, text="synthetic")
    request = AsyncMock(side_effect=post)
    result = asyncio.run(delete_chat_result(_client(request), "c_fixture"))
    assert not result.ok and result.error.code.value == "AUTH_REQUIRED" and result.data is None
    assert request.await_count == 1


def test_cleanup_manager_uses_single_sdk_dispatch_and_keeps_durable_failure(monkeypatch, tmp_path):
    from src.infrastructure.account_identity import current_account_key
    from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account")
    post = AsyncMock(return_value=SimpleNamespace(status_code=500))
    client = _client(post)
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=current_account_key,
                                       cleanup_guard=lambda _cid: False)
    owner = current_account_key()
    manager.schedule_cleanup_result("c_fixture", dispatch=False)
    asyncio.run(manager.delete_chat_result("c_fixture", client=client))
    assert post.await_count == 1
    assert manager._store.get(owner, "c_fixture")["state"] == "pending"


def test_manager_injected_account_provider_reaches_real_sdk_without_global_env(monkeypatch, tmp_path):
    from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    post = AsyncMock(return_value=SimpleNamespace(status_code=500))
    manager = RemoteChatCleanupManager(store_path=tmp_path / "cleanup.sqlite3",
                                       account_key_provider=lambda: "injected-account",
                                       cleanup_guard=lambda _cid: False)
    manager.schedule_cleanup_result("c_fixture", dispatch=False)
    asyncio.run(manager.delete_chat_result("c_fixture", client=_client(post)))
    assert post.await_count == 1
    assert manager._store.get("injected-account", "c_fixture")["state"] == "pending"
