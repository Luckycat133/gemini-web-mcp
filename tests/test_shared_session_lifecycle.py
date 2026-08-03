"""Cross-adapter evidence for the single shared session lifecycle."""

import asyncio
from types import SimpleNamespace

from src.adapters.mcp_sdk import MCPServer

import src.client_wrapper as client_wrapper
import src.skill_server as skill_server
import src.tools.chat as chat_tools
from src.session_manager import SessionService


class _FakeSession:
    def __init__(self, cid):
        self.cid = cid
        self.prompts = []

    async def send_message(self, **kwargs):
        prompt = kwargs["prompt"]
        self.prompts.append(prompt)
        return SimpleNamespace(
            text=f"reply:{prompt}",
            images=[],
            videos=[],
            media=[],
            metadata=[self.cid],
        )


class _FakeClient:
    def __init__(self):
        self.sessions = []

    def start_chat(self, model=None, gem=None):
        session = _FakeSession(f"c_{len(self.sessions) + 1}")
        self.sessions.append(session)
        return session


def _patch_adapter_environment(monkeypatch):
    """Wire both adapters to one real service and one fake upstream client."""
    service = SessionService()
    client = _FakeClient()
    deleted_cids = []
    monkeypatch.setattr(client_wrapper, "_session_manager", service)

    async def noop_initialize():
        return None

    async def noop_cleanup(_client):
        return 0

    async def fake_delete(cid):
        deleted_cids.append(cid)
        return True

    for module in (chat_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", noop_initialize)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", noop_cleanup)

    monkeypatch.setattr(client_wrapper, "delete_remote_chat", fake_delete)
    monkeypatch.setattr(chat_tools, "schedule_remote_chat_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        skill_server,
        "schedule_remote_chat_cleanup_from_response",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup", lambda *args, **kwargs: None)
    return service, client, deleted_cids


async def _call_primary(mcp, name, **kwargs):
    return (await mcp.call_tool(name, kwargs)).content


def test_primary_create_is_sendable_and_resettable_from_compact_adapter(monkeypatch):
    service, client, deleted_cids = _patch_adapter_environment(monkeypatch)
    primary = MCPServer("primary-test")
    chat_tools.register_chat_tools(primary)

    async def run():
        created = await _call_primary(
            primary,
            "gemini_start_chat",
            model="flash",
            retain_chat=True,
        )
        session_id = created[0].text.split("ID: ", 1)[1].splitlines()[0]

        compact_list = await skill_server.session("list")
        assert session_id in compact_list[0].text

        sent = await skill_server.session("send", session_id=session_id, message="from compact")
        assert "reply:from compact" in sent[0].text

        reset = await skill_server.session("reset_one", session_id=session_id)
        assert reset[0].text == f"Session deleted: {session_id}"
        return session_id

    session_id = asyncio.run(run())
    assert service.lookup_session(session_id).ok is False
    assert client.sessions[0].prompts == ["from compact"]
    assert deleted_cids == []  # retain_chat=True survives either adapter's reset.


def test_compact_create_is_sendable_and_resettable_from_primary_adapter(monkeypatch):
    service, client, deleted_cids = _patch_adapter_environment(monkeypatch)
    primary = MCPServer("primary-test")
    chat_tools.register_chat_tools(primary)

    async def run():
        created = await skill_server.session("create", model="pro")
        session_id = created[0].text.removeprefix("Session created: ")

        sent = await _call_primary(
            primary,
            "gemini_send_message",
            session_id=session_id,
            message="from primary",
        )
        assert sent[0].text == "reply:from primary"

        reset = await _call_primary(primary, "gemini_reset_session", session_id=session_id)
        assert reset[0].text == f"✅ 会话 {session_id} 已重置"

        compact_list = await skill_server.session("list")
        assert compact_list[0].text == "No active sessions"
        return session_id

    session_id = asyncio.run(run())
    assert service.lookup_session(session_id).ok is False
    assert client.sessions[0].prompts == ["from primary"]
    assert deleted_cids == ["c_1"]


def test_unknown_single_reset_never_clears_other_adapter_sessions(monkeypatch):
    service, _client, _deleted_cids = _patch_adapter_environment(monkeypatch)

    async def run():
        created = await skill_server.session("create")
        session_id = created[0].text.removeprefix("Session created: ")

        missing = await skill_server.session("reset", session_id="sess_missing")
        assert "SESSION_NOT_FOUND" in missing[0].text

        ambiguous = await skill_server.session("reset")
        assert ambiguous[0].text.startswith("INVALID_ARGUMENT")
        return session_id

    session_id = asyncio.run(run())
    assert service.lookup_session(session_id).ok is True
