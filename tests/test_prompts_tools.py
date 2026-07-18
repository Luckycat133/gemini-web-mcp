"""tools/prompts 模块的 PromptManager 类与 gemini_manage_prompts 工具行为测试。

调研发现该模块覆盖率仅 48%（136 stmts, 71 miss），无专用测试文件。此前仅
test_tool_workflows.py::test_prompt_list_exposes_full_id_for_cleanup 间接覆盖
了 create + list 的 happy path，关键行为契约零断言：

- PromptManager._load_prompts：文件不存在 / JSON 解析异常 / 正常加载既有数据
- PromptManager._save_prompts：写入异常（目录不存在）吞咽不崩溃
- PromptManager.create_prompt：返回 uuid、持久化、字段齐全
- PromptManager.get_prompt：命中 / 未命中
- PromptManager.list_prompts：空 / 无分类过滤 / 按分类过滤 / 按 created_at 降序
- PromptManager.list_categories：空 / 多分类排序
- PromptManager.update_prompt：未找到 / 部分更新 / 全量更新 / updated_at 刷新
- PromptManager.delete_prompt：未找到 / 命中删除
- get_prompt_manager：单例创建一次 / 线程安全
- gemini_manage_prompts 6 个 action + invalid action + 异常兜底

文件系统隔离：每个需要持久化的测试用 tmp_path 构造独立 PromptManager；
单例测试 monkeypatch DEFAULT_PROMPTS_FILE 到 tmp_path 并在 finally 重置
_prompt_manager = None，防止跨测试污染。
"""

import asyncio
import json
import threading

from mcp.server.fastmcp import FastMCP

import src.tools.prompts as prompts_tools


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


async def _call_tool(mcp, tool_name, **kwargs):
    content, _structured = await mcp.call_tool(tool_name, kwargs)
    return content


def _make_mcp():
    mcp = FastMCP("test")
    prompts_tools.register_prompts_tools(mcp)
    return mcp


def _set_singleton(manager):
    """设置模块级单例，供工具调用时 get_prompt_manager() 返回。"""
    prompts_tools._prompt_manager = manager


def _reset_singleton():
    prompts_tools._prompt_manager = None


# ---------------------------------------------------------------------------
# PromptManager._load_prompts
# ---------------------------------------------------------------------------


def test_load_prompts_skips_when_file_absent(tmp_path):
    """文件不存在 → prompts 为空，不抛异常。"""
    target = tmp_path / "absent.json"
    mgr = prompts_tools.PromptManager(str(target))
    assert mgr.prompts == {}


def test_load_prompts_swallows_invalid_json_and_logs(tmp_path, caplog):
    """文件存在但 JSON 非法 → 记录错误日志，prompts 回退为空。"""
    target = tmp_path / "bad.json"
    target.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level("ERROR", logger="src.tools.prompts"):
        mgr = prompts_tools.PromptManager(str(target))
    assert mgr.prompts == {}
    assert any("加载提示词失败" in rec.message for rec in caplog.records)


def test_load_prompts_reads_existing_prompts(tmp_path):
    """文件含合法 JSON → 加载 prompts 字典。"""
    target = tmp_path / "existing.json"
    target.write_text(
        json.dumps({
            "version": "1.0",
            "prompts": {
                "p1": {"id": "p1", "name": "A", "content": "ca",
                       "category": "x", "description": "",
                       "created_at": "2026-01-01T00:00:00",
                       "updated_at": "2026-01-01T00:00:00"},
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    mgr = prompts_tools.PromptManager(str(target))
    assert "p1" in mgr.prompts
    assert mgr.prompts["p1"]["name"] == "A"


# ---------------------------------------------------------------------------
# PromptManager._save_prompts
# ---------------------------------------------------------------------------


def test_save_prompts_swallows_write_exception(tmp_path, caplog):
    """写入到不存在目录 → open 抛 FileNotFoundError，被吞咽并记录日志。"""
    target = tmp_path / "no_such_dir" / "prompts.json"
    mgr = prompts_tools.PromptManager(str(target))
    with caplog.at_level("ERROR", logger="src.tools.prompts"):
        mgr._save_prompts()
    assert any("保存提示词失败" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# PromptManager.create_prompt
# ---------------------------------------------------------------------------


def test_create_prompt_returns_uuid_and_persists(tmp_path):
    """create_prompt 返回 uuid 字符串，写入内存字典并持久化到文件。"""
    target = tmp_path / "p.json"
    mgr = prompts_tools.PromptManager(str(target))
    pid = mgr.create_prompt(name="N", content="C", category="cat", description="D")
    assert isinstance(pid, str) and len(pid) == 36  # uuid4 字符串长度
    assert pid in mgr.prompts
    entry = mgr.prompts[pid]
    assert entry["id"] == pid
    assert entry["name"] == "N"
    assert entry["content"] == "C"
    assert entry["category"] == "cat"
    assert entry["description"] == "D"
    assert "created_at" in entry and "updated_at" in entry
    # 持久化到文件
    data = json.loads(target.read_text(encoding="utf-8"))
    assert pid in data["prompts"]
    assert data["version"] == "1.0"


def test_create_prompt_uses_default_category_and_description(tmp_path):
    """未传 category/description → 默认 '通用' / ''。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="N", content="C")
    assert mgr.prompts[pid]["category"] == "通用"
    assert mgr.prompts[pid]["description"] == ""


# ---------------------------------------------------------------------------
# PromptManager.get_prompt
# ---------------------------------------------------------------------------


def test_get_prompt_returns_entry_when_found(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="N", content="C")
    result = mgr.get_prompt(pid)
    assert result is not None
    assert result["name"] == "N"


def test_get_prompt_returns_none_when_missing(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    assert mgr.get_prompt("nope") is None


# ---------------------------------------------------------------------------
# PromptManager.list_prompts
# ---------------------------------------------------------------------------


def test_list_prompts_empty_returns_empty_list(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    assert mgr.list_prompts() == []


def test_list_prompts_without_category_returns_all_sorted_desc(tmp_path):
    """无 category → 返回全部，按 created_at 降序。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    # 手动注入不同 created_at 以验证排序
    mgr.prompts = {
        "a": {"id": "a", "name": "A", "category": "x",
              "created_at": "2026-01-01T00:00:00"},
        "b": {"id": "b", "name": "B", "category": "y",
              "created_at": "2026-02-01T00:00:00"},
        "c": {"id": "c", "name": "C", "category": "x",
              "created_at": "2026-03-01T00:00:00"},
    }
    result = mgr.list_prompts()
    assert [p["id"] for p in result] == ["c", "b", "a"]


def test_list_prompts_with_category_filters_and_sorts(tmp_path):
    """有 category → 仅返回该分类，仍按 created_at 降序。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    mgr.prompts = {
        "a": {"id": "a", "name": "A", "category": "x",
              "created_at": "2026-01-01T00:00:00"},
        "b": {"id": "b", "name": "B", "category": "y",
              "created_at": "2026-02-01T00:00:00"},
        "c": {"id": "c", "name": "C", "category": "x",
              "created_at": "2026-03-01T00:00:00"},
    }
    result = mgr.list_prompts(category="x")
    assert [p["id"] for p in result] == ["c", "a"]


# ---------------------------------------------------------------------------
# PromptManager.list_categories
# ---------------------------------------------------------------------------


def test_list_categories_empty_returns_empty_list(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    assert mgr.list_categories() == []


def test_list_categories_returns_sorted_unique(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    mgr.prompts = {
        "a": {"id": "a", "category": "zeta"},
        "b": {"id": "b", "category": "alpha"},
        "c": {"id": "c", "category": "zeta"},  # 重复
    }
    assert mgr.list_categories() == ["alpha", "zeta"]


# ---------------------------------------------------------------------------
# PromptManager.update_prompt
# ---------------------------------------------------------------------------


def test_update_prompt_returns_false_when_id_missing(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    assert mgr.update_prompt("nope", name="X") is False


def test_update_prompt_partial_name_only(tmp_path):
    """仅传 name → 只更新 name，其他字段不变。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="Old", content="C", category="cat", description="D")
    ok = mgr.update_prompt(pid, name="New")
    assert ok is True
    entry = mgr.prompts[pid]
    assert entry["name"] == "New"
    assert entry["content"] == "C"
    assert entry["category"] == "cat"
    assert entry["description"] == "D"


def test_update_prompt_all_fields_and_refreshes_updated_at(tmp_path):
    """全量更新 → 所有字段更新，updated_at 刷新。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="Old", content="OC", category="oc", description="OD")
    old_updated = mgr.prompts[pid]["updated_at"]
    ok = mgr.update_prompt(pid, name="New", content="NC", category="nc", description="ND")
    assert ok is True
    entry = mgr.prompts[pid]
    assert entry["name"] == "New"
    assert entry["content"] == "NC"
    assert entry["category"] == "nc"
    assert entry["description"] == "ND"
    assert entry["updated_at"] >= old_updated


def test_update_prompt_allows_empty_string_values(tmp_path):
    """显式传空字符串 → 更新为空（is not None 检查，非 falsy 检查）。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="N", content="C", description="D")
    ok = mgr.update_prompt(pid, name="", content="", description="")
    assert ok is True
    entry = mgr.prompts[pid]
    assert entry["name"] == ""
    assert entry["content"] == ""
    assert entry["description"] == ""


# ---------------------------------------------------------------------------
# PromptManager.delete_prompt
# ---------------------------------------------------------------------------


def test_delete_prompt_returns_false_when_id_missing(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    assert mgr.delete_prompt("nope") is False


def test_delete_prompt_removes_entry_and_persists(tmp_path):
    target = tmp_path / "p.json"
    mgr = prompts_tools.PromptManager(str(target))
    pid = mgr.create_prompt(name="N", content="C")
    ok = mgr.delete_prompt(pid)
    assert ok is True
    assert pid not in mgr.prompts
    data = json.loads(target.read_text(encoding="utf-8"))
    assert pid not in data["prompts"]


# ---------------------------------------------------------------------------
# get_prompt_manager 单例 + 线程安全
# ---------------------------------------------------------------------------


def test_get_prompt_manager_creates_singleton_once(monkeypatch, tmp_path):
    """首次调用创建实例，后续返回同一实例。

    注意：DEFAULT_PROMPTS_FILE 在类定义时绑定为 __init__ 默认参数，
    monkeypatch 模块级常量无法改变已绑定的默认值。因此改为 patch
    PromptManager 类本身，用子类硬编码 tmp_path 避免 CWD 污染。
    """
    target = tmp_path / "prompts.json"

    class _TmpMgr(prompts_tools.PromptManager):
        def __init__(self):
            super().__init__(str(target))

    monkeypatch.setattr(prompts_tools, "PromptManager", _TmpMgr)
    _reset_singleton()
    try:
        m1 = prompts_tools.get_prompt_manager()
        m2 = prompts_tools.get_prompt_manager()
        assert m1 is m2
        assert m1.file_path == str(target)
    finally:
        _reset_singleton()


def test_get_prompt_manager_is_thread_safe(monkeypatch, tmp_path):
    """8 线程并发调用 get_prompt_manager → 返回同一实例（验证 _prompt_manager_lock）。"""
    target = tmp_path / "prompts.json"

    class _TmpMgr(prompts_tools.PromptManager):
        def __init__(self):
            super().__init__(str(target))

    monkeypatch.setattr(prompts_tools, "PromptManager", _TmpMgr)
    _reset_singleton()
    try:
        results: list = []

        def worker():
            results.append(prompts_tools.get_prompt_manager())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 8
        assert all(r is results[0] for r in results)
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — list action
# ---------------------------------------------------------------------------


def test_tool_list_empty_returns_placeholder(tmp_path):
    """空提示词库 → 返回 '暂无提示词。'。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(mcp, "gemini_manage_prompts", action="list")

        text = asyncio.run(run())[0].text
        assert "暂无提示词" in text
    finally:
        _reset_singleton()


def test_tool_list_with_category_filter_empty_returns_placeholder(tmp_path):
    """有 category 但无匹配 → 返回 '暂无提示词 (分类: {category})。'。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    mgr.create_prompt(name="N", content="C", category="other")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="list", category="missing"
            )

        text = asyncio.run(run())[0].text
        assert "暂无提示词" in text
        assert "分类: missing" in text
    finally:
        _reset_singleton()


def test_tool_list_renders_entries_with_category_header_and_description(tmp_path):
    """非空 list → 渲染标题、分类头、编号条目（含 description 行）。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="My Prompt", content="C", category="work",
                            description="A useful prompt")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="list", category="work"
            )

        text = asyncio.run(run())[0].text
        assert "## 📝 预设提示词" in text
        assert "**分类**: work" in text
        assert "1. My Prompt (ID: " in text
        assert f"ID: {pid}" in text
        assert "分类: work" in text
        assert "描述: A useful prompt" in text
    finally:
        _reset_singleton()


def test_tool_list_omits_description_line_when_absent(tmp_path):
    """条目无 description → 不渲染 '描述:' 行。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    mgr.create_prompt(name="NoDesc", content="C", category="g", description="")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(mcp, "gemini_manage_prompts", action="list")

        text = asyncio.run(run())[0].text
        assert "描述:" not in text
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — list_categories action
# ---------------------------------------------------------------------------


def test_tool_list_categories_empty_returns_placeholder(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="list_categories"
            )

        text = asyncio.run(run())[0].text
        assert "暂无分类" in text
    finally:
        _reset_singleton()


def test_tool_list_categories_renders_counts(tmp_path):
    """非空 → 渲染编号分类列表，含每类条目数。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    mgr.create_prompt(name="A", content="c", category="beta")
    mgr.create_prompt(name="B", content="c", category="alpha")
    mgr.create_prompt(name="C", content="c", category="beta")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="list_categories"
            )

        text = asyncio.run(run())[0].text
        assert "## 🏷️ 提示词分类" in text
        assert "alpha (1 个提示词)" in text
        assert "beta (2 个提示词)" in text
        # 排序：alpha 在 beta 前
        assert text.index("alpha") < text.index("beta")
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — get action
# ---------------------------------------------------------------------------


def test_tool_get_missing_prompt_id_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(mcp, "gemini_manage_prompts", action="get")

        text = asyncio.run(run())[0].text
        assert "需要提供 prompt_id" in text
    finally:
        _reset_singleton()


def test_tool_get_not_found_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="get", prompt_id="missing"
            )

        text = asyncio.run(run())[0].text
        assert "未找到 ID 为 missing 的提示词" in text
    finally:
        _reset_singleton()


def test_tool_get_renders_full_detail(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="Detail", content="body text", category="cat",
                            description="a desc")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="get", prompt_id=pid
            )

        text = asyncio.run(run())[0].text
        assert "## Detail" in text
        assert f"**ID**: {pid}" in text
        assert "**分类**: cat" in text
        assert "**创建时间**" in text
        assert "**更新时间**" in text
        assert "a desc" in text
        assert "body text" in text
    finally:
        _reset_singleton()


def test_tool_get_renders_no_description_placeholder_when_absent(tmp_path):
    """prompt 缺 description 键 → .get('description', '无描述') 返回默认值 '无描述'。

    注意：create_prompt 总会写入 description（空串也算），所以此分支只能通过
    手动注入一个缺 description 键的 prompt 字典来触发（模拟旧版数据文件）。
    """
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = "manual-no-desc"
    mgr.prompts[pid] = {
        "id": pid, "name": "N", "content": "C", "category": "g",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        # 故意不设 description 键
    }
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="get", prompt_id=pid
            )

        text = asyncio.run(run())[0].text
        assert "无描述" in text
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — create action
# ---------------------------------------------------------------------------


def test_tool_create_missing_name_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="create", content="C"
            )

        text = asyncio.run(run())[0].text
        assert "需要提供 name 和 content" in text
    finally:
        _reset_singleton()


def test_tool_create_missing_content_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="create", name="N"
            )

        text = asyncio.run(run())[0].text
        assert "需要提供 name 和 content" in text
    finally:
        _reset_singleton()


def test_tool_create_success_with_default_category(tmp_path):
    """未传 category → 默认 '通用'，返回 ID 与名称。"""
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="create",
                name="Tool Prompt", content="body",
            )

        text = asyncio.run(run())[0].text
        assert "提示词创建成功" in text
        assert "名称: Tool Prompt" in text
        assert "分类: 通用" in text
        assert "ID:" in text
    finally:
        _reset_singleton()


def test_tool_create_success_with_explicit_category(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="create",
                name="N", content="C", category="custom",
            )

        text = asyncio.run(run())[0].text
        assert "分类: custom" in text
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — update action
# ---------------------------------------------------------------------------


def test_tool_update_missing_prompt_id_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(mcp, "gemini_manage_prompts", action="update")

        text = asyncio.run(run())[0].text
        assert "需要提供 prompt_id" in text
    finally:
        _reset_singleton()


def test_tool_update_not_found_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="update",
                prompt_id="missing", name="X",
            )

        text = asyncio.run(run())[0].text
        assert "未找到 ID 为 missing 的提示词" in text
    finally:
        _reset_singleton()


def test_tool_update_success_returns_confirmation(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="Old", content="C")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="update",
                prompt_id=pid, name="New", content="NC",
            )

        text = asyncio.run(run())[0].text
        assert f"提示词 {pid} 更新成功" in text
        assert mgr.prompts[pid]["name"] == "New"
        assert mgr.prompts[pid]["content"] == "NC"
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — delete action
# ---------------------------------------------------------------------------


def test_tool_delete_missing_prompt_id_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(mcp, "gemini_manage_prompts", action="delete")

        text = asyncio.run(run())[0].text
        assert "需要提供 prompt_id" in text
    finally:
        _reset_singleton()


def test_tool_delete_not_found_returns_error(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="delete", prompt_id="missing"
            )

        text = asyncio.run(run())[0].text
        assert "未找到 ID 为 missing 的提示词" in text
    finally:
        _reset_singleton()


def test_tool_delete_success_returns_confirmation(tmp_path):
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    pid = mgr.create_prompt(name="N", content="C")
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(
                mcp, "gemini_manage_prompts", action="delete", prompt_id=pid
            )

        text = asyncio.run(run())[0].text
        assert f"提示词 {pid} 删除成功" in text
        assert pid not in mgr.prompts
    finally:
        _reset_singleton()


# ---------------------------------------------------------------------------
# gemini_manage_prompts — invalid action + 异常兜底
# ---------------------------------------------------------------------------


def test_tool_invalid_action_via_mcp_raises_tool_error(tmp_path):
    """FastMCP 在 dispatch 前用 pydantic 校验 Literal，action='bogus' 抛 ToolError，
    不会进入函数体。这验证了类型注解的防御性校验生效。"""
    from mcp.server.fastmcp.exceptions import ToolError

    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            try:
                await _call_tool(mcp, "gemini_manage_prompts", action="bogus")
                raise AssertionError("should have raised ToolError")
            except ToolError as e:
                return str(e)

        msg = asyncio.run(run())
        assert "Input should be" in msg or "literal_error" in msg
    finally:
        _reset_singleton()


def test_tool_invalid_action_fallback_via_direct_fn_call(tmp_path):
    """直接调用 tool.fn(action='bogus') 绕过 MCP pydantic 校验 → 触发 line 251
    的 '❌ 无效的 action。' 兜底分支。

    通过 mcp._tool_manager.get_tool(...).fn 访问注册的原始函数，跳过参数校验。
    该分支在生产中经由 MCP 不可达（Literal 校验先行），但作为函数内防御性
    fallback 仍需覆盖。
    """
    mgr = prompts_tools.PromptManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()
        tool = mcp._tool_manager.get_tool("gemini_manage_prompts")

        async def run():
            return await tool.fn(action="bogus")

        text = asyncio.run(run())[0].text
        assert "无效的 action" in text
    finally:
        _reset_singleton()


def test_tool_exception_fallback_returns_failure_message(tmp_path, caplog):
    """manager 方法抛异常 → 被 except Exception 捕获，返回 '❌ 失败: {e}'。"""

    class _ExplodingManager(prompts_tools.PromptManager):
        def list_prompts(self, category=None):
            raise RuntimeError("boom")

    mgr = _ExplodingManager(str(tmp_path / "p.json"))
    _set_singleton(mgr)
    try:
        mcp = _make_mcp()

        async def run():
            return await _call_tool(mcp, "gemini_manage_prompts", action="list")

        with caplog.at_level("ERROR", logger="src.tools.prompts"):
            text = asyncio.run(run())[0].text
        assert "❌ 失败: boom" in text
        assert any("提示词操作失败" in rec.message for rec in caplog.records)
    finally:
        _reset_singleton()
