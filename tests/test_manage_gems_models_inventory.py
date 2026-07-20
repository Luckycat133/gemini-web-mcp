"""manage.py Gems/Models/Account Inventory handler + helpers 行为契约测试。

调研发现 manage.py 中以下 helper 与 MCP handler 的关键分支此前仅
``test_tool_workflows.py`` 单点 happy-path 间接覆盖，边界分支零断言：

1. ``_iter_gem_values`` [2160-2165]：3 分支（空 / dict.values / list）
2. ``_find_gem_by_id`` [2168-2176]：3 分支（dict.get 命中 / 迭代命中 / 未命中）
3. ``_gem_field`` [2179-2187]：3 分支（dict 命中 / attr 命中 / 未命中）
4. ``gemini_manage_gems`` [3822-3936]：list 空 / create 无 name / update 无 gem_id
   / update partial gem 未找到 / update partial 缺字段 / delete 无 gem_id
   / invalid action / 顶层 except / happy path list — 9 个分支
5. ``gemini_list_models`` [3797-3820]：异常 → models=None / 无 list_models attr
   → models=None / happy path 渲染 — 3 分支
6. ``gemini_account_inventory`` [2931-2974]：11 个 surface dispatch + invalid 兜底
   （本文件覆盖 4 个代表性 surface）
7. ``gemini_read_chat`` [2475-2504]：无 chat_id / 无 read_chat / history None
   / 顶层 except
8. ``gemini_export_chat`` [2639-2673]：无 chat_id / 无 read_chat / history None
   / metadata warning
9. ``gemini_delete_chat`` [2744-2754]：无 chat_id / 无 delete_chat / 顶层 except
   / happy path

mock 边界：
- client_wrapper 接缝：``get_gemini_client`` / ``initialize_client``
- ``gemini_export_chat`` 内部接缝：``_read_chat_turns`` /
  ``_fetch_recent_conversation_metadata``
- 调用方式：MCP handler 经 ``register_manage_tools(mcp, layers=["all"])``
  注册后通过 ``mcp.call_tool`` 分发；对于 Literal 类型校验会拒绝的
  invalid action 分支，直接通过 ``mcp._tool_manager.get_tool(...).fn``
  调用底层函数以绕开 pydantic 校验。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from mcp.server.fastmcp import FastMCP

import src.tools.manage as manage_tools


# ---------------------------------------------------------------------------
# 共享辅助
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


class _NoBatchClient:
    """无任何 RPC 能力的 client，触发能力缺失早退。"""

    pass


class _ModelsClient:
    """带 ``list_models`` 的 client，用于 ``gemini_list_models`` 测试。"""

    def __init__(self, models=None, raise_exc=None):
        self._models = models if models is not None else []
        self._raise_exc = raise_exc

    def list_models(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._models


class _GemClient:
    """带 gems CRUD 方法的 client（fetch_gems/create_gem/update_gem/delete_gem）。"""

    def __init__(self, gems=None, created_gem=None, raise_exc=None):
        self._gems = gems
        self._created_gem = created_gem
        self._raise_exc = raise_exc
        self.update_calls = []
        self.delete_calls = []

    async def fetch_gems(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._gems

    async def create_gem(self, name, prompt, description):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._created_gem

    async def update_gem(self, gem, name, prompt, description):
        self.update_calls.append({
            "gem": gem, "name": name, "prompt": prompt, "description": description,
        })
        return None

    async def delete_gem(self, gem_id):
        self.delete_calls.append(gem_id)
        return None


class _ReadChatClient:
    """带 ``read_chat`` 的 client，用于 read/export chat 测试。"""

    def __init__(self, history=None, raise_exc=None):
        self._history = history
        self._raise_exc = raise_exc

    async def read_chat(self, chat_id, limit=20):
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._history


class _ReadChatBatchClient(_ReadChatClient):
    """带 ``read_chat`` 和 ``_batch_execute`` 的 client，用于 metadata 路径测试。"""

    async def _batch_execute(self, raw_rpc_list, *, source_path=None, close_on_error=False):
        raise RuntimeError("batch unavailable")


class _DeleteChatClient:
    """带 ``delete_chat`` 的 client，用于 ``gemini_delete_chat`` 测试。"""

    def __init__(self, raise_exc=None):
        self._raise_exc = raise_exc
        self.delete_calls = []

    async def delete_chat(self, chat_id):
        if self._raise_exc is not None:
            raise self._raise_exc
        self.delete_calls.append(chat_id)
        return None


def _patch_seams(monkeypatch, client):
    """统一 patch manage 模块的 client_wrapper 接缝。"""
    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None

    monkeypatch.setattr(manage_tools, "initialize_client", fake_init)


def _make_mcp():
    mcp = FastMCP("test")
    manage_tools.register_manage_tools(mcp, layers=["all"])
    return mcp


async def _call(mcp, name, **kwargs):
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


async def _call_raw(mcp, name, **kwargs):
    """绕开 pydantic Literal 校验，直接调用底层 handler 函数。"""
    tool = mcp._tool_manager.get_tool(name)
    return await tool.fn(**kwargs)


def _model(display_name, model_name, is_available=True, description="desc"):
    return SimpleNamespace(
        display_name=display_name,
        model_name=model_name,
        is_available=is_available,
        description=description,
    )


def _gem(gem_id, name, description="", prompt=""):
    return SimpleNamespace(
        id=gem_id, name=name, description=description, prompt=prompt,
    )


# ===========================================================================
# Section A: _iter_gem_values + _find_gem_by_id + _gem_field
# ===========================================================================


def test_iter_gem_values_empty_returns_empty_list():
    """空输入（None / 空 dict / 空 list）→ 返回 []。"""
    assert manage_tools._iter_gem_values(None) == []
    assert manage_tools._iter_gem_values({}) == []
    assert manage_tools._iter_gem_values([]) == []


def test_iter_gem_values_dict_returns_values_list():
    """dict 输入（有 .values()）→ 返回 values 列表。"""
    g1 = _gem("g1", "Gem 1")
    g2 = _gem("g2", "Gem 2")
    result = manage_tools._iter_gem_values({"g1": g1, "g2": g2})
    assert result == [g1, g2]


def test_iter_gem_values_list_returns_copy():
    """list 输入（无 .values()）→ 返回新的 list（副本）。"""
    g1 = _gem("g1", "Gem 1")
    g2 = _gem("g2", "Gem 2")
    gems = [g1, g2]
    result = manage_tools._iter_gem_values(gems)
    assert result == [g1, g2]
    assert result is not gems  # 必须是新 list


def test_find_gem_by_id_dict_get_hit():
    """dict.get(gem_id) 直接命中 → 返回该 gem（不进入迭代）。"""
    g1 = _gem("g1", "Gem 1")
    g2 = _gem("g2", "Gem 2")
    gems = {"g1": g1, "g2": g2}
    assert manage_tools._find_gem_by_id(gems, "g1") is g1
    assert manage_tools._find_gem_by_id(gems, "g2") is g2


def test_find_gem_by_id_iterate_hit():
    """list 输入 + 迭代匹配 _gem_field(gem, 'id', 'gem_id') → 命中。"""
    g1 = _gem("alpha", "Gem Alpha")
    g2 = _gem("beta", "Gem Beta")
    gems = [g1, g2]
    assert manage_tools._find_gem_by_id(gems, "beta") is g2


def test_find_gem_by_id_miss_returns_none():
    """未命中 → 返回 None。"""
    g1 = _gem("g1", "Gem 1")
    assert manage_tools._find_gem_by_id([g1], "missing") is None
    assert manage_tools._find_gem_by_id({"g1": g1}, "missing") is None
    assert manage_tools._find_gem_by_id(None, "missing") is None


def test_gem_field_dict_hit():
    """gem 是 dict 且字段非 None → (True, str(value))。"""
    gem = {"id": "g1", "name": "Gem One"}
    found, value = manage_tools._gem_field(gem, "name")
    assert found is True
    assert value == "Gem One"


def test_gem_field_attr_hit():
    """gem 是对象 + 字段属性非 None → (True, str(value))。"""
    gem = _gem("g1", "Gem One")
    found, value = manage_tools._gem_field(gem, "name")
    assert found is True
    assert value == "Gem One"


def test_gem_field_miss_returns_false_empty():
    """所有 name 都未命中 → (False, '')。"""
    gem = _gem("g1", "Gem One")
    found, value = manage_tools._gem_field(gem, "nonexistent_field")
    assert found is False
    assert value == ""
    # dict 缺字段同样未命中
    found, value = manage_tools._gem_field({"id": "g1"}, "name")
    assert found is False
    assert value == ""


# ===========================================================================
# Section B: gemini_manage_gems
# ===========================================================================


def test_manage_gems_list_empty(monkeypatch):
    """action=list 且 fetch_gems 返回空 → '暂无保存的 Gems。'"""
    client = _GemClient(gems=[])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="list"))
    assert result[0].text == "暂无保存的 Gems。"


def test_manage_gems_create_no_name(monkeypatch):
    """action=create 但未提供 name → '❌ 创建 Gem 需要提供名称。'"""
    client = _GemClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="create"))
    assert result[0].text == "❌ 创建 Gem 需要提供名称。"


def test_manage_gems_update_no_gem_id(monkeypatch):
    """action=update 但未提供 gem_id → '❌ 更新 Gem 需要提供 gem_id。'"""
    client = _GemClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="update"))
    assert result[0].text == "❌ 更新 Gem 需要提供 gem_id。"


def test_manage_gems_update_partial_gem_not_found(monkeypatch):
    """action=update 提供 gem_id 但缺字段 → fetch_gems 后 _find_gem_by_id 未命中。

    期望返回 '❌ 局部更新 Gem 前需要读取现有 Gem，但未找到该 gem_id...'。
    """
    client = _GemClient(gems=[])  # 空 gems 列表，找不到 gem_id
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="update", gem_id="missing"))
    assert "❌ 局部更新 Gem 前需要读取现有 Gem" in result[0].text
    assert "未找到该 gem_id" in result[0].text
    assert client.update_calls == []  # 未调用 update_gem


def test_manage_gems_update_partial_missing_field(monkeypatch):
    """action=update 提供 gem_id + 部分字段（name=None），fetch_gems 命中
    但 existing_gem 缺 name 字段 → '❌ 局部更新 Gem 缺少现有字段: name'。"""
    # existing gem 没有 name 字段
    existing = SimpleNamespace(id="g1", description="d", prompt="p")
    client = _GemClient(gems=[existing])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="update", gem_id="g1"))
    assert "❌ 局部更新 Gem 缺少现有字段" in result[0].text
    assert "name" in result[0].text
    assert client.update_calls == []  # 未调用 update_gem


def test_manage_gems_delete_no_gem_id(monkeypatch):
    """action=delete 但未提供 gem_id → '❌ 删除 Gem 需要提供 gem_id。'"""
    client = _GemClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="delete"))
    assert result[0].text == "❌ 删除 Gem 需要提供 gem_id。"


def test_manage_gems_invalid_action(monkeypatch):
    """action 不是 list/create/update/delete → '❌ 无效的 action。'。

    FastMCP 的 pydantic Literal 校验会拒绝非法 action，因此通过
    ``mcp._tool_manager.get_tool(...).fn`` 直接调用底层函数绕开校验，
    覆盖该兜底分支。
    """
    client = _GemClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call_raw(mcp, "gemini_manage_gems", action="foo"))
    assert result[0].text == "❌ 无效的 action。"


def test_manage_gems_top_level_exception(monkeypatch):
    """action=list 但 fetch_gems 抛异常 → 顶层 except '❌ 失败: {e}'。"""
    client = _GemClient(raise_exc=RuntimeError("gem rpc down"))
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="list"))
    assert "❌ 失败" in result[0].text
    assert "gem rpc down" in result[0].text


def test_manage_gems_happy_path_list(monkeypatch):
    """action=list 且 fetch_gems 返回非空 → markdown 渲染 '## 💎 Gems 列表' + 条目。"""
    g1 = _gem("g1", "Gem One", description="First gem description")
    g2 = _gem("g2", "Gem Two", description="Second gem description")
    client = _GemClient(gems=[g1, g2])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_manage_gems", action="list"))
    text = result[0].text
    assert text.startswith("## 💎 Gems 列表")
    assert "1. Gem One (ID: g1)" in text
    assert "First gem description" in text
    assert "2. Gem Two (ID: g2)" in text


# ===========================================================================
# Section C: gemini_list_models
# ===========================================================================


def test_list_models_exception_returns_aliases_with_no_models(monkeypatch):
    """list_models 抛异常 → models=None → 返回 aliases + '暂无运行时模型注册表'。"""
    client = _ModelsClient(raise_exc=RuntimeError("boom"))
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_models"))
    text = result[0].text
    assert "MCP 模型别名" in text
    assert "暂无运行时模型注册表" in text


def test_list_models_no_list_models_attr_returns_no_models(monkeypatch):
    """client 无 list_models 属性 → models=None → 返回 aliases + 暂无提示。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_models"))
    text = result[0].text
    assert "MCP 模型别名" in text
    assert "暂无运行时模型注册表" in text


def test_list_models_happy_path_renders_models(monkeypatch):
    """client.list_models() 返回非空 → markdown 渲染每个模型的别名行。"""
    m1 = _model("Flash Lite", "gemini-3-flash-lite", is_available=True, description="极速模型")
    m2 = _model("Pro", "gemini-3-pro", is_available=False, description="高级模型")
    client = _ModelsClient(models=[m1, m2])
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_list_models"))
    text = result[0].text
    assert "MCP 模型别名" in text
    assert "- Flash Lite: gemini-3-flash-lite (可用)" in text
    assert "极速模型" in text
    assert "- Pro: gemini-3-pro (不可用)" in text
    assert "高级模型" in text


# ===========================================================================
# Section D: gemini_account_inventory surface dispatch
# ===========================================================================


def test_account_inventory_surface_models_dispatches_to_list_models(monkeypatch):
    """surface=models → 调用 gemini_list_models（无 list_models → 暂无运行时模型注册表）。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_account_inventory", surface="models"))
    text = result[0].text
    assert "MCP 模型别名" in text
    assert "暂无运行时模型注册表" in text


def test_account_inventory_surface_modes_dispatches_to_tool_mode_status(monkeypatch):
    """surface=modes → 调用 gemini_get_tool_mode_status（无 _batch_execute → 早退）。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_account_inventory", surface="modes"))
    assert "❌ 当前客户端不支持工具模式状态 RPC" in result[0].text


def test_account_inventory_surface_capabilities_dispatches_to_web_capabilities(monkeypatch):
    """surface=capabilities → 调用 gemini_get_web_capabilities（静态 markdown）。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_account_inventory", surface="capabilities"))
    text = result[0].text
    assert text.startswith("## Gemini Web Pro 能力清单")


def test_account_inventory_surface_links_dispatches_to_list_public_links(monkeypatch):
    """surface=links → 调用 gemini_list_public_links（无 _batch_execute → 早退）。"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_account_inventory", surface="links"))
    assert "❌ 当前客户端不支持公开链接 RPC" in result[0].text


# ===========================================================================
# Section E: gemini_read_chat / gemini_export_chat / gemini_delete_chat
# ===========================================================================


def test_read_chat_no_chat_id(monkeypatch):
    """空 chat_id → '❌ 读取聊天需要提供 chat_id。'"""
    client = _ReadChatClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_read_chat", chat_id=""))
    assert result[0].text == "❌ 读取聊天需要提供 chat_id。"


def test_read_chat_no_read_chat_attr(monkeypatch):
    """client 无 read_chat → '❌ 当前 gemini-webapi 不支持 read_chat。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_read_chat", chat_id="chat_1"))
    assert result[0].text == "❌ 当前 gemini-webapi 不支持 read_chat。"


def test_read_chat_history_none(monkeypatch):
    """read_chat 返回 None → '未找到聊天: {chat_id}'。"""
    client = _ReadChatClient(history=None)
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_read_chat", chat_id="chat_42"))
    assert result[0].text == "未找到聊天: chat_42"


def test_read_chat_top_level_exception(monkeypatch):
    """read_chat 抛异常 → 顶层 except '❌ 读取失败: {e}'。"""
    client = _ReadChatClient(raise_exc=RuntimeError("auth expired"))
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_read_chat", chat_id="chat_1"))
    assert "❌ 读取失败" in result[0].text
    assert "auth expired" in result[0].text


def test_export_chat_no_chat_id(monkeypatch):
    """空 chat_id → '❌ 导出聊天需要提供 chat_id。'"""
    client = _ReadChatClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_export_chat", chat_id=""))
    assert result[0].text == "❌ 导出聊天需要提供 chat_id。"


def test_export_chat_no_read_chat_attr(monkeypatch):
    """client 无 read_chat → '❌ 当前 gemini-webapi 不支持 read_chat。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_export_chat", chat_id="chat_1"))
    assert result[0].text == "❌ 当前 gemini-webapi 不支持 read_chat。"


def test_export_chat_history_none(monkeypatch):
    """read_chat 返回 None → '未找到聊天: {chat_id}'。"""
    history = None
    client = _ReadChatClient(history=history)
    _patch_seams(monkeypatch, client)
    # _read_chat_turns 内部调用 client.read_chat → 返回 None
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_export_chat", chat_id="chat_99"))
    assert result[0].text == "未找到聊天: chat_99"


def test_export_chat_metadata_warning(monkeypatch):
    """include_metadata=True 且 _fetch_recent_conversation_metadata 抛异常
    → payload.metadata 含 'metadata_warning' 字段。"""
    history = SimpleNamespace(cid="chat_1", turns=[
        SimpleNamespace(role="user", text="hello"),
    ])
    client = _ReadChatBatchClient(history=history)
    _patch_seams(monkeypatch, client)
    monkeypatch.setattr(
        manage_tools, "_fetch_recent_conversation_metadata",
        AsyncMock(side_effect=RuntimeError("metadata rpc down")),
    )
    mcp = _make_mcp()
    result = _run(_call(
        mcp, "gemini_export_chat", chat_id="chat_1",
        response_format="json", include_metadata=True,
    ))
    payload = json.loads(result[0].text)
    assert payload["metadata"]["id"] == "chat_1"
    assert "metadata_warning" in payload["metadata"]
    assert "RuntimeError" in payload["metadata"]["metadata_warning"]
    assert "metadata rpc down" in payload["metadata"]["metadata_warning"]


def test_delete_chat_no_chat_id(monkeypatch):
    """空 chat_id → '❌ 删除聊天需要提供 chat_id。'"""
    client = _DeleteChatClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_chat", chat_id=""))
    assert result[0].text == "❌ 删除聊天需要提供 chat_id。"


def test_delete_chat_no_delete_chat_attr(monkeypatch):
    """client 无 delete_chat → '❌ 当前 gemini-webapi 不支持 delete_chat。'"""
    client = _NoBatchClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_chat", chat_id="chat_1"))
    assert result[0].text == "❌ 当前 gemini-webapi 不支持 delete_chat。"


def test_delete_chat_top_level_exception(monkeypatch):
    """delete_chat 抛异常 → 顶层 except '❌ 删除失败: {e}'。"""
    client = _DeleteChatClient(raise_exc=RuntimeError("network down"))
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_chat", chat_id="chat_1"))
    assert "❌ 删除失败" in result[0].text
    assert "network down" in result[0].text


def test_delete_chat_happy_path(monkeypatch):
    """delete_chat 成功 → '✅ 已删除聊天: {chat_id}'。"""
    client = _DeleteChatClient()
    _patch_seams(monkeypatch, client)
    mcp = _make_mcp()
    result = _run(_call(mcp, "gemini_delete_chat", chat_id="chat_1"))
    assert result[0].text == "✅ 已删除聊天: chat_1"
    assert client.delete_calls == ["chat_1"]
