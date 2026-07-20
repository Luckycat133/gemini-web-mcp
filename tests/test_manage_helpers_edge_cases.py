"""manage.py helper 函数 edge cases 行为契约测试。

覆盖 manage.py 中 12 个内部 helper 的边界条件与异常路径，此前仅通过 handler
间接覆盖，关键 edge case（None / 非预期类型 / 边界值 / 空输入）零直接断言：

1. ``_clamp_int`` [186]：int 范围内 / 超出上下限 / None / 非数字 str / 数字 str。
2. ``_paginate_items`` [195]：分页切片 + page_info（total_count / count / offset /
   limit / has_more / next_offset），含空 items 与非法 limit/offset。
3. ``_chat_to_dict`` [230]：SimpleNamespace → dict（cid→id, title, is_pinned,
   timestamp, time），含缺失字段回退。
4. ``_parse_public_link_entry`` [470]：raw list → dict（id / title / disabled / url /
   field_count），含非 str 字段与空 list。
5. ``_parse_library_capability`` [509]：raw list → dict（aliases 过滤非 str / name /
   description / details）。
6. ``_parse_conversation_metadata`` [593]：raw list → dict（id / title / is_pinned /
   timestamp / bot_id），含 None is_pinned 回退。
7. ``_find_notebook`` [954]：by id 优先于 title / casefold + whitespace strip。
8. ``_turn_matches_query`` [284]：role / text 大小写不敏感匹配，含空字段。
9. ``_read_chat_turns`` [275]：无 read_chat → RuntimeError / turns 非列表 → 空。
10. ``_parse_tool_mode_entry`` [1221]：非 list → raw_type。
11. ``_parse_usage_entry`` [482]：非 list / reset 仅 seconds 无 nanos。
12. ``_format_chat_export_markdown`` [311]：metadata 存在 / 缺失 → title 回退 chat_id。

mock 边界：helper 直接调用，不经 MCP 分发。
"""

import asyncio
from types import SimpleNamespace

import src.tools.manage as manage_tools


def _run(coro):
    return asyncio.run(coro)


# ===========================================================================
# Section A: _clamp_int
# ===========================================================================


def test_clamp_int_in_range_returns_value():
    """int 值在 [minimum, maximum] 内 → 原值返回。"""
    assert manage_tools._clamp_int(5, default=10, minimum=1, maximum=100) == 5


def test_clamp_int_above_max_returns_max():
    """值超过 maximum → 返回 maximum。"""
    assert manage_tools._clamp_int(200, default=10, minimum=1, maximum=100) == 100


def test_clamp_int_none_returns_default():
    """None → int(None) 抛 TypeError → 回退 default，再 clamp 到 [1, 100]。"""
    assert manage_tools._clamp_int(None, default=42, minimum=1, maximum=100) == 42


def test_clamp_int_numeric_string_parses():
    """数字 str "5" → int(5)。"""
    assert manage_tools._clamp_int("5", default=10, minimum=1, maximum=100) == 5


# ===========================================================================
# Section B: _paginate_items
# ===========================================================================


def test_paginate_items_normal_page_with_has_more():
    """limit < len(items) → page 切片，has_more=True，next_offset=offset+count。"""
    items = [1, 2, 3, 4, 5]
    page, info = manage_tools._paginate_items(items, limit=2, offset=1)
    assert page == [2, 3]
    assert info["total_count"] == 5
    assert info["count"] == 2
    assert info["offset"] == 1
    assert info["limit"] == 2
    assert info["has_more"] is True
    assert info["next_offset"] == 3


def test_paginate_items_last_page_no_has_more():
    """offset+limit >= len(items) → has_more=False，next_offset=None。"""
    items = [1, 2, 3]
    page, info = manage_tools._paginate_items(items, limit=5, offset=0)
    assert page == [1, 2, 3]
    assert info["has_more"] is False
    assert info["next_offset"] is None


def test_paginate_items_empty_items():
    """空 items → page=[]，has_more=False，next_offset=None。"""
    page, info = manage_tools._paginate_items([], limit=10, offset=0)
    assert page == []
    assert info["total_count"] == 0
    assert info["count"] == 0
    assert info["has_more"] is False
    assert info["next_offset"] is None


def test_paginate_items_clamps_invalid_limit_offset():
    """limit=-5 → clamp 1；offset=999 → clamp max(len(items), 0)=3。"""
    items = [1, 2, 3]
    page, info = manage_tools._paginate_items(items, limit=-5, offset=999)
    assert info["limit"] == 1
    assert info["offset"] == 3
    assert page == []


# ===========================================================================
# Section C: _chat_to_dict
# ===========================================================================


def test_chat_to_dict_full_namespace():
    """SimpleNamespace(cid, title, is_pinned, timestamp) → 完整 dict。"""
    chat = SimpleNamespace(cid="c_1", title="Title", is_pinned=True, timestamp=1000)
    result = manage_tools._chat_to_dict(chat)
    assert result["id"] == "c_1"
    assert result["title"] == "Title"
    assert result["is_pinned"] is True
    assert result["timestamp"] == 1000
    assert result["time"]  # _format_timestamp(1000) 非空


def test_chat_to_dict_defaults_for_missing_fields():
    """缺少 cid/title/is_pinned/timestamp → id="", title="Untitled", is_pinned=False, time=""。"""
    chat = SimpleNamespace()
    result = manage_tools._chat_to_dict(chat)
    assert result["id"] == ""
    assert result["title"] == "Untitled"
    assert result["is_pinned"] is False
    assert result["timestamp"] is None
    assert result["time"] == ""


# ===========================================================================
# Section D: _parse_public_link_entry
# ===========================================================================


def test_parse_public_link_entry_non_list_returns_raw_type():
    """非 list → {'raw_type': ...}。"""
    result = manage_tools._parse_public_link_entry("not_a_list")
    assert result == {"raw_type": "str"}


def test_parse_public_link_entry_full_list():
    """完整 [id, title, disabled, _, url] → 全字段解析。"""
    entry = ["id_1", "Link Title", False, "skip", "https://example.com"]
    result = manage_tools._parse_public_link_entry(entry)
    assert result["id"] == "id_1"
    assert result["title"] == "Link Title"
    assert result["disabled"] is False
    assert result["url"] == "https://example.com"
    assert result["field_count"] == 5


def test_parse_public_link_entry_empty_and_non_str_fields():
    """空 list → 全默认值；非 str 字段 → 空字符串。"""
    result = manage_tools._parse_public_link_entry([])
    assert result["id"] == ""
    assert result["title"] == ""
    assert result["disabled"] is False
    assert result["url"] == ""
    assert result["field_count"] == 0

    # 非 str 字段 → id/title/url 为空，disabled 经 bool() 转换
    entry = [123, 456, True, None, None]
    result = manage_tools._parse_public_link_entry(entry)
    assert result["id"] == ""
    assert result["title"] == ""
    assert result["disabled"] is True
    assert result["url"] == ""


# ===========================================================================
# Section E: _parse_library_capability
# ===========================================================================


def test_parse_library_capability_non_list_returns_raw_type():
    """非 list → {'raw_type': ...}。"""
    result = manage_tools._parse_library_capability({"not": "a list"})
    assert result == {"raw_type": "dict"}


def test_parse_library_capability_filters_non_str_aliases():
    """aliases 列表中非 str 元素被过滤掉。"""
    entry = [["alias_a", 123, "alias_b", None], "Cap Name", "description", "details"]
    result = manage_tools._parse_library_capability(entry)
    assert result["aliases"] == ["alias_a", "alias_b"]
    assert result["name"] == "Cap Name"
    assert result["description"] == "description"
    assert result["details"] == "details"
    assert result["field_count"] == 4


# ===========================================================================
# Section F: _parse_conversation_metadata
# ===========================================================================


def test_parse_conversation_metadata_non_list_returns_raw_type():
    """非 list → {'raw_type': ...}。"""
    result = manage_tools._parse_conversation_metadata(42)
    assert result == {"raw_type": "int"}


def test_parse_conversation_metadata_full_with_timestamp_and_bot_id():
    """完整 entry 含 timestamp=[seconds, nanos] 和 bot_id。"""
    entry = ["c_1", "Title", True, None, None, [1000, 5e8], None, "bot_123"]
    result = manage_tools._parse_conversation_metadata(entry)
    assert result["id"] == "c_1"
    assert result["title"] == "Title"
    assert result["is_pinned"] is True
    assert result["timestamp"] == 1000.5  # 1000 + 0.5e9/1e9
    assert result["time"]  # _format_timestamp 非空
    assert result["bot_id"] == "bot_123"
    assert result["field_count"] == 8


def test_parse_conversation_metadata_none_is_pinned_defaults_false():
    """entry[2]=None → is_pinned=False。"""
    entry = ["c_1", "Title", None]
    result = manage_tools._parse_conversation_metadata(entry)
    assert result["is_pinned"] is False


# ===========================================================================
# Section G: _find_notebook
# ===========================================================================


def test_find_notebook_id_takes_priority_over_title():
    """同时提供 id 和 title → 优先按 id 匹配。"""
    notebooks = [
        {"id": "n_1", "title": "Math"},
        {"id": "n_2", "title": "Different"},
    ]
    result = manage_tools._find_notebook(notebooks, notebook_id="n_1", notebook_title="Different")
    assert result == {"id": "n_1", "title": "Math"}


def test_find_notebook_casefold_with_whitespace():
    """title 带空白 strip 后 casefold 匹配。"""
    notebooks = [{"id": "n_1", "title": "Math"}]
    result = manage_tools._find_notebook(notebooks, notebook_title="  math  ")
    assert result == {"id": "n_1", "title": "Math"}


# ===========================================================================
# Section H: _turn_matches_query
# ===========================================================================


def test_turn_matches_query_role_only_with_empty_text():
    """text 为空但 role 匹配 → True。"""
    turn = {"role": "user", "text": ""}
    assert manage_tools._turn_matches_query(turn, "user") is True


def test_turn_matches_query_text_only_with_empty_role():
    """role 为空但 text 匹配 → True。"""
    turn = {"role": "", "text": "find needle here"}
    assert manage_tools._turn_matches_query(turn, "needle") is True


# ===========================================================================
# Section I: _read_chat_turns
# ===========================================================================


def test_read_chat_turns_without_read_chat_raises():
    """client 无 read_chat → RuntimeError。"""
    client = SimpleNamespace()
    raised = False
    try:
        _run(manage_tools._read_chat_turns(client, "c_1", 20, 1000))
    except RuntimeError as e:
        raised = True
        assert "read_chat" in str(e)
    assert raised


def test_read_chat_turns_turns_raw_not_list_returns_empty():
    """history.turns 非 list → turns=[]。"""
    client = SimpleNamespace()

    async def read_chat(chat_id, limit=20):
        return SimpleNamespace(cid=chat_id, turns="not_a_list")

    client.read_chat = read_chat
    history, turns = _run(manage_tools._read_chat_turns(client, "c_1", 20, 1000))
    assert history is not None
    assert turns == []


# ===========================================================================
# Section J: _parse_tool_mode_entry
# ===========================================================================


def test_parse_tool_mode_entry_non_list_returns_raw_type():
    """非 list → {'raw_type': ...}。"""
    result = manage_tools._parse_tool_mode_entry(None)
    assert result == {"raw_type": "NoneType"}


# ===========================================================================
# Section K: _parse_usage_entry
# ===========================================================================


def test_parse_usage_entry_non_list_returns_raw_type():
    """非 list → {'raw_type': ...}。"""
    result = manage_tools._parse_usage_entry(3.14)
    assert result == {"raw_type": "float"}


def test_parse_usage_entry_reset_with_only_seconds_no_nanos():
    """reset=[seconds] 无 nanos → nanos=0，timestamp=seconds。"""
    entry = ["key", "ok", "free", [2000], 100, 80]
    result = manage_tools._parse_usage_entry(entry)
    assert result["reset_timestamp"] == 2000.0  # 2000 + 0/1e9
    assert result["reset_time"]  # 非空
    assert result["limit_value"] == 100
    assert result["remaining_value"] == 80


# ===========================================================================
# Section L: _format_chat_export_markdown
# ===========================================================================


def test_format_chat_export_markdown_with_metadata_title_and_time():
    """metadata 含 title + time → 渲染标题行 + Time 行。"""
    payload = {
        "chat_id": "c_1",
        "count": 1,
        "turns": [{"role": "user", "text": "hello"}],
        "metadata": {"title": "My Chat", "time": "2025-01-01 00:00:00 UTC"},
    }
    result = manage_tools._format_chat_export_markdown(payload)
    assert "## Gemini Chat Export: My Chat" in result
    assert "Chat ID: c_1" in result
    assert "Turns: 1" in result
    assert "Time: 2025-01-01 00:00:00 UTC" in result
    assert "### 1. user" in result
    assert "hello" in result


def test_format_chat_export_markdown_without_metadata_falls_back_to_chat_id():
    """metadata=None → title 回退 chat_id，无 Time 行。"""
    payload = {
        "chat_id": "c_42",
        "count": 0,
        "turns": [],
    }
    result = manage_tools._format_chat_export_markdown(payload)
    assert "## Gemini Chat Export: c_42" in result
    assert "Chat ID: c_42" in result
    assert "Turns: 0" in result
    assert "Time:" not in result
