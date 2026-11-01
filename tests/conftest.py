"""Pytest root conftest for the tests/ suite.

The manage tests use ``tests._fastmcp_shim.FastMCP`` instead of the real
``fastmcp`` package: the project pins ``mcp>=2.0.0,<3``, but every published
fastmcp release was built against mcp 1.x and cannot be imported under mcp 2.x.
The shim exercises the real ``register_manage_tools`` registration and the real
tool handlers, so no compatibility shimming is required here.
"""

import pytest


@pytest.fixture(autouse=True)
def isolated_operation_database(monkeypatch, tmp_path):
    """Offline tests must never write to the user's real operation ledger."""
    monkeypatch.setenv("GEMINI_OPERATION_DB_PATH", str(tmp_path / "operations.sqlite3"))
    monkeypatch.setenv("GEMINI_CLEANUP_DB_PATH", str(tmp_path / "cleanup.sqlite3"))
    monkeypatch.setattr("src.remote_chat_cleanup_manager.default_account_key", lambda: "test-account")
    from src import client_wrapper
    from src.remote_chat_cleanup_manager import RemoteChatCleanupManager
    monkeypatch.setattr(client_wrapper, "_cleanup_manager", RemoteChatCleanupManager(
        store_path=tmp_path / "cleanup.sqlite3", account_key_provider=lambda: "test-account",
        client_provider=lambda: client_wrapper._client_manager.get_client(),
        retention_provider=client_wrapper.get_default_chat_retention_seconds,
    ))
