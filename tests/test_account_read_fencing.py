import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp import Client

from src.adapters.mcp_sdk import MCPServer
from src.onboarding import domain_result_from_call
from src.tools import manage
from src import skill_server


def _server():
    server = MCPServer("account-read-fence")
    manage.register_history_tools(server, {"gemini_history", "gemini_read_chat", "gemini_export_chat"}, compact=True)
    manage.register_notebook_tools(server, {"gemini_notebooks"}, default_source="gemini")
    manage.register_notebook_tools(server, {"gemini_move_chat_to_notebook"})
    manage.register_scheduled_tools(server, {"gemini_get_scheduled_action", "gemini_delete_scheduled_action"})
    return server


async def _call(server, tool, arguments):
    async with Client(server, mode="auto", cache=None) as client:
        return await client.call_tool(tool, arguments)


def test_history_turn_switch_blocks_followup_and_scrubs_both_text_and_metadata(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-a")
    async def read(_cid, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-b")
        return SimpleNamespace(cid="c_one", turns=[SimpleNamespace(role="model", text="old-account-private-marker")])
    fake = SimpleNamespace(list_chats=lambda: [SimpleNamespace(cid="c_one", title="fixture"),
                                              SimpleNamespace(cid="c_two", title="fixture")],
                           read_chat=AsyncMock(side_effect=read))
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    monkeypatch.setattr(manage, "get_gemini_client", lambda: fake)
    result = asyncio.run(_call(_server(), "gemini_history", {"action": "search", "query": "marker", "scan_turns": True}))
    parsed = domain_result_from_call(result)
    assert parsed["ok"] is False and parsed["error"]["code"] == "AUTH_REQUIRED"
    assert "old-account-private-marker" not in str(result) and fake.read_chat.await_count == 1


def test_notebook_catalog_switch_stops_before_next_page_and_scrubs(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-a")
    async def batch(*_args, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-b")
        return SimpleNamespace(status_code=200, text="old-account-private-marker")
    fake = SimpleNamespace(_batch_execute=AsyncMock(side_effect=batch))
    monkeypatch.setattr(manage, "initialize_client", AsyncMock())
    monkeypatch.setattr(manage, "get_gemini_client", lambda: fake)
    result = asyncio.run(_call(_server(), "gemini_notebooks", {"source": "gemini", "action": "chats", "notebook_id": "fixture"}))
    parsed = domain_result_from_call(result)
    assert parsed["ok"] is False and parsed["error"]["code"] == "AUTH_REQUIRED"
    assert "old-account-private-marker" not in str(result) and fake._batch_execute.await_count == 1


def test_compact_history_switch_returns_auth_required_without_private_content(monkeypatch):
    monkeypatch.setenv("GEMINI_PSID", "synthetic-a")
    async def read(_cid, **_kwargs):
        monkeypatch.setenv("GEMINI_PSID", "synthetic-b")
        return SimpleNamespace(cid="c_one", turns=[SimpleNamespace(role="model", text="old-account-private-marker")])
    fake = SimpleNamespace(read_chat=AsyncMock(side_effect=read))
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock())
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: fake)
    content = asyncio.run(skill_server.history(action="read", chat_id="c_one"))
    assert content[0].meta["domain_result"]["error"]["code"] == "AUTH_REQUIRED"
    assert "old-account-private-marker" not in str(content)


def test_three_blank_targets_are_typed_and_do_not_initialize(monkeypatch):
    initializer = AsyncMock()
    monkeypatch.setattr(manage, "initialize_client", initializer)
    async def run():
        server = _server()
        for tool, args in (("gemini_move_chat_to_notebook", {"chat_id": "", "notebook_id": "fixture"}),
                           ("gemini_get_scheduled_action", {"action_id": ""}),
                           ("gemini_delete_scheduled_action", {"action_id": ""})):
            result = await _call(server, tool, args)
            parsed = domain_result_from_call(result)
            assert not parsed["ok"] and parsed["error"]["code"] == "INVALID_ARGUMENT"
    asyncio.run(run())
    assert initializer.await_count == 0


def test_history_allows_first_initialization_binding_but_rejects_account_switch(monkeypatch):
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    async def bind():
        monkeypatch.setenv("GEMINI_PSID", "synthetic-account")
    monkeypatch.setattr(manage, "initialize_client", AsyncMock(side_effect=bind))
    monkeypatch.setattr(manage, "get_gemini_client", lambda: SimpleNamespace(list_chats=lambda: []))
    result = asyncio.run(_call(_server(), "gemini_history", {"action": "list"}))
    assert domain_result_from_call(result)["ok"] is True
    monkeypatch.setenv("GEMINI_PSID", "synthetic-account-a")
    async def switch():
        monkeypatch.setenv("GEMINI_PSID", "synthetic-account-b")
    monkeypatch.setattr(manage, "initialize_client", AsyncMock(side_effect=switch))
    result = asyncio.run(_call(_server(), "gemini_history", {"action": "list"}))
    assert domain_result_from_call(result)["error"]["code"] == "AUTH_REQUIRED"


def test_notebooklm_invalid_request_is_rejected_before_initialization(monkeypatch):
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    initializer = AsyncMock()
    monkeypatch.setattr(manage, "initialize_client", initializer)
    server = MCPServer("notebook-validation")
    manage.register_notebook_tools(server, {"gemini_notebooks"}, default_source="notebooklm")
    async def run():
        invalid_limit = domain_result_from_call(await _call(server, "gemini_notebooks", {"limit": 0}))
        unsupported = domain_result_from_call(await _call(server, "gemini_notebooks", {"action": "chats"}))
        assert invalid_limit["error"]["code"] == "INVALID_ARGUMENT"
        assert unsupported["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    asyncio.run(run())
    assert initializer.await_count == 0


def test_blank_history_read_and_export_reject_before_initialization(monkeypatch):
    monkeypatch.delenv("GEMINI_PSID", raising=False)
    initializer = AsyncMock()
    monkeypatch.setattr(manage, "initialize_client", initializer)
    async def run():
        server = _server()
        for tool, args in (("gemini_read_chat", {"chat_id": ""}),
                           ("gemini_export_chat", {"chat_id": ""}),
                           ("gemini_history", {"action": "read", "chat_id": ""}),
                           ("gemini_history", {"action": "export", "chat_id": ""})):
            result = await _call(server, tool, args)
            parsed = domain_result_from_call(result)
            assert not parsed["ok"] and parsed["error"]["code"] == "INVALID_ARGUMENT"
            assert "chat_id" in result.content[0].text
    asyncio.run(run())
    assert initializer.await_count == 0
