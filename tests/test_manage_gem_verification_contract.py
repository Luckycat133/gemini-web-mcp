"""Regression contracts for truthful Gem mutation presentation and identity handling."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tests._fastmcp_shim import FastMCP

import src.tools.manage as manage_tools
from src.services.gems import GemMutationNotVerified, create_gem, delete_gem, update_gem


def _run(coro):
    return asyncio.run(coro)


def _patch_manage(monkeypatch, client):
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: client)

    async def initialize():
        return None

    monkeypatch.setattr(manage_tools, "initialize_client", initialize)


def _make_mcp():
    mcp = FastMCP("gem-verification")
    manage_tools.register_manage_tools(mcp, layers=["all"])
    return mcp


async def _call(mcp, **kwargs):
    content, _structured = await mcp.call_tool("gemini_manage_gems", kwargs)
    return content[0].text


def test_mapping_backed_gems_render_name_id_and_description(monkeypatch):
    class Client:
        async def fetch_gems(self):
            return {
                "g-map": {
                    "id": "g-map",
                    "name": "Mapping Gem",
                    "description": "mapping-backed",
                    "prompt": "help",
                }
            }

    _patch_manage(monkeypatch, Client())
    text = _run(_call(_make_mcp(), action="list"))

    assert "Mapping Gem (ID: g-map)" in text
    assert "mapping-backed" in text
    assert "Untitled" not in text


def test_create_rejects_whitespace_only_name_before_remote_call(monkeypatch):
    class Client:
        called = False

        async def create_gem(self, **_kwargs):
            self.called = True

    client = Client()
    _patch_manage(monkeypatch, client)
    text = _run(_call(_make_mcp(), action="create", name="   "))

    assert text.startswith("❌ 失败:")
    assert "must not be empty" in text
    assert client.called is False


def test_create_requires_verified_read_back(monkeypatch):
    class Client:
        async def create_gem(self, name, prompt, description):
            return SimpleNamespace(id="g-pending", name=name, prompt=prompt, description=description or "")

        async def fetch_gems(self):
            return {}

    client = Client()
    with_error = None
    try:
        _run(create_gem(client, name="Writer", description="", instructions="Write"))
    except GemMutationNotVerified as error:
        with_error = error

    assert with_error is not None
    assert with_error.verification_status == "read_back_not_observed"
    assert "g-pending" in str(with_error)

    _patch_manage(monkeypatch, client)
    text = _run(_call(_make_mcp(), action="create", name="Writer", instructions="Write"))
    assert text.startswith("❌ 失败:")
    assert "g-pending" in text
    assert "✅" not in text


def test_verified_create_keeps_success_text(monkeypatch):
    class Client:
        def __init__(self):
            self.gems = {}

        async def create_gem(self, name, prompt, description):
            gem = SimpleNamespace(id="g-created", name=name, prompt=prompt, description=description or "")
            self.gems[gem.id] = gem
            return gem

        async def fetch_gems(self):
            return self.gems

    _patch_manage(monkeypatch, Client())
    text = _run(_call(_make_mcp(), action="create", name="Writer", instructions="Write"))
    assert text.startswith("✅ Gem 创建成功")
    assert "读回校验: verified" in text


def test_update_mismatch_is_not_presented_as_verified_success(monkeypatch):
    class Client:
        async def update_gem(self, **_kwargs):
            return None

        async def fetch_gems(self):
            return {
                "g1": SimpleNamespace(
                    id="g1",
                    name="Old",
                    description="Old",
                    prompt="Old",
                )
            }

    client = Client()
    with_error = None
    try:
        _run(update_gem(client, gem_id="g1", name="New", description="New", instructions="New"))
    except GemMutationNotVerified as error:
        with_error = error

    assert with_error is not None
    assert with_error.verification_status == "read_back_mismatch"
    assert set(with_error.mismatched_fields) == {"name", "description", "instructions"}

    _patch_manage(monkeypatch, client)
    text = _run(
        _call(
            _make_mcp(),
            action="update",
            gem_id="g1",
            name="New",
            description="New",
            instructions="New",
        )
    )
    assert text.startswith("❌ 失败:")
    assert "更新成功" in text
    assert "未获读回验证" in text
    assert "✅" not in text


def test_verified_update_keeps_success_text(monkeypatch):
    class Client:
        def __init__(self):
            self.gems = {
                "g1": SimpleNamespace(id="g1", name="Old", description="Old", prompt="Old")
            }

        async def update_gem(self, gem, name, prompt, description):
            self.gems[gem] = SimpleNamespace(
                id=gem,
                name=name,
                description=description,
                prompt=prompt,
            )

        async def fetch_gems(self):
            return self.gems

    _patch_manage(monkeypatch, Client())
    text = _run(
        _call(
            _make_mcp(),
            action="update",
            gem_id="g1",
            name="New",
            description="New",
            instructions="New",
        )
    )
    assert text.startswith("✅ Gem g1 更新成功")
    assert "读回校验: verified" in text


def test_delete_still_present_is_not_presented_as_success(monkeypatch):
    class Client:
        async def delete_gem(self, _gem_id):
            return None

        async def fetch_gems(self):
            return {"g1": SimpleNamespace(id="g1", name="Still here")}

    client = Client()
    with_error = None
    try:
        _run(delete_gem(client, gem_id="g1"))
    except GemMutationNotVerified as error:
        with_error = error

    assert with_error is not None
    assert with_error.verification_status == "still_present"

    _patch_manage(monkeypatch, client)
    text = _run(_call(_make_mcp(), action="delete", gem_id="g1"))
    assert text.startswith("❌ 失败:")
    assert "未获已删除证据" in text
    assert "✅" not in text


def test_verified_delete_keeps_success_text(monkeypatch):
    class Client:
        async def delete_gem(self, _gem_id):
            return None

        async def fetch_gems(self):
            return {}

    _patch_manage(monkeypatch, Client())
    text = _run(_call(_make_mcp(), action="delete", gem_id="g1"))
    assert text.startswith("✅ Gem g1 删除成功")
    assert "读回校验: verified_deleted" in text
