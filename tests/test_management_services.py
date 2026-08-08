"""Shared management mutation services expose read-back verification."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.services.gems import create_gem, delete_gem, update_gem
from src.services.notebooks import move_chat_to_notebook


def test_gem_mutations_report_verified_read_back():
    class Client:
        def __init__(self):
            self.gems = {}

        async def fetch_gems(self):
            return self.gems

        async def create_gem(self, name, prompt, description=""):
            gem = SimpleNamespace(id="gem-1", name=name, prompt=prompt, description=description or "")
            self.gems[gem.id] = gem
            return gem

        async def update_gem(self, gem, name, prompt, description=""):
            self.gems[gem] = SimpleNamespace(
                id=gem,
                name=name,
                prompt=prompt,
                description=description or "",
            )

        async def delete_gem(self, gem):
            self.gems.pop(gem, None)

    async def run():
        client = Client()
        created = await create_gem(
            client,
            name="Writer",
            description="Drafts",
            instructions="Write clearly",
        )
        assert created["verification_status"] == "verified"

        updated = await update_gem(
            client,
            gem_id="gem-1",
            name="Editor",
            description="Revises",
            instructions="Edit carefully",
        )
        assert updated["verification_status"] == "verified"
        assert updated["mismatched_fields"] == []

        deleted = await delete_gem(client, gem_id="gem-1")
        assert deleted["verification_status"] == "verified_deleted"

    asyncio.run(run())


def test_notebook_move_reports_verified_read_back():
    class Client:
        async def _batch_execute(self, payloads, *, source_path, close_on_error):
            assert payloads[0].rpcid
            assert source_path == "/app"
            assert close_on_error is False
            return SimpleNamespace(status_code=200, text="response")

    async def fetch_notebooks(_client, _locale):
        return ([{"id": "notebook-1", "title": "Work", "project_type": 2}], {})

    async def fetch_chats(_client, _notebook_id, _limit, _offset):
        return ([{"id": "chat-1", "title": "Chat"}], {"count": 1})

    async def run():
        result = await move_chat_to_notebook(
            Client(),
            chat_id="chat-1",
            notebook_id="notebook-1",
            fetch_notebooks=fetch_notebooks,
            fetch_chats=fetch_chats,
            extract_bodies=lambda _text, _rpc_id: [[None, ["chat-1", "Chat"]]],
        )
        assert result["ok"] is True
        assert result["verification_status"] == "verified"
        assert result["verified_in_target_notebook"] is True

    asyncio.run(run())
