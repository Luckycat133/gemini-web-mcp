"""research 模块的纯 helper / immersive 报告提取 / 入口工具行为契约测试。

调研发现 `tests/test_research_tools.py` 仅覆盖 `gemini_deep_research` 入口
（native / fallback / 错误分支 33 个测试），但下列函数零直接覆盖：

- 渲染 helper：`_render_research_artifact` 分发 + 6 个 `_render_*` /
  `_markdown_sections` / `_iter_source_links` / `_plain_excerpt` /
  `_title_from_markdown` / `_safe_filename`
- 报告动作 / artifact 入口 helper：`_research_report_actions_payload` /
  `_format_research_report_actions` / `_create_research_report_artifact` /
  `_format_research_report_artifact`
- 沉浸式报告提取：`_walk_nested_json` / `_extract_sources_from_node` /
  `_extract_deep_research_immersive_report` / `_fetch_deep_research_immersive_report` /
  `_request_completed_research_report`
- Native recovery：`_create_deep_research_plan` capability probe false negative 恢复 /
  `_start_deep_research_with_recovery` 超时回退 / `_start_fresh_research_chat`
  清理 attr 异常吞咽 / `_resolve_deep_research_transport_model` ImportError/None 分支 /
  `_is_default_deep_research_transport` / `_is_capability_probe_false_negative` /
  `_is_research_start_message` / `_is_research_completion_message`
- 入口工具：`gemini_list_research_report_actions`（无报告 / json / markdown）/
  `gemini_create_from_research_report`（无报告 / json / markdown / 各 artifact_type）
- `_format_deep_research_result` 边界（not done + start message 清空 / not done 无 report /
  done 无 report 走 plan_text 回退）

不可测试的分支：`except ImportError`（gemini_webapi 在 venv 中恒可用）——
其中 `_fetch_deep_research_immersive_report` 的 ImportError 用 monkeypatch 模拟。
"""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp.server.fastmcp import FastMCP

import src.tools.research as research_tools
from src.tools.research import (
    _create_deep_research_plan,
    _create_research_report_artifact,
    _extract_deep_research_immersive_report,
    _extract_sources_from_node,
    _fetch_deep_research_immersive_report,
    _format_deep_research_result,
    _format_research_report_actions,
    _format_research_report_artifact,
    _is_capability_probe_false_negative,
    _is_default_deep_research_transport,
    _is_research_completion_message,
    _is_research_start_message,
    _iter_source_links,
    _markdown_sections,
    _plain_excerpt,
    _render_research_artifact,
    _request_completed_research_report,
    _research_report_actions_payload,
    _resolve_deep_research_transport_model,
    _safe_filename,
    _start_deep_research_with_recovery,
    _start_fresh_research_chat,
    _title_from_markdown,
    _wait_for_deep_research_by_chat,
    _walk_nested_json,
)


def _run(coro):
    return asyncio.run(coro)


def _long_report(body_seed: str = "## Section A\n", *, length: int = 1100) -> str:
    """构造 ≥1000 字符且含 '## ' 标记的报告正文（满足 _extract_deep_research_immersive_report 阈值）。"""
    body = body_seed + ("x" * length)
    return body


# ---------------------------------------------------------------------------
# A. 纯渲染 helper
# ---------------------------------------------------------------------------


def test_markdown_sections_splits_on_headings():
    """_markdown_sections 按 '## ' heading 切分。"""
    report = "## Intro\nbody intro\n## Findings\nbody findings\n## Wrap\nwrap body"
    sections = _markdown_sections(report)
    assert [s["heading"] for s in sections] == ["Intro", "Findings", "Wrap"]
    assert sections[0]["body"] == "body intro"


def test_markdown_sections_drops_empty_body_sections():
    """空 body 的 section 被过滤。"""
    report = "## Empty\n\n## Has\ncontent"
    sections = _markdown_sections(report)
    assert [s["heading"] for s in sections] == ["Has"]


def test_markdown_sections_returns_empty_when_no_heading():
    """无 heading → 空列表。"""
    assert _markdown_sections("just plain text no headings") == []


def test_iter_source_links_filters_non_dict_and_non_http():
    """非 dict / 非 http URL 被过滤。"""
    sources = [
        {"url": "https://ok.com/a", "title": "Ok A"},
        "not-a-dict",
        {"url": "ftp://no-ftp.com", "title": "Ftp"},
        {"url": "https://ok.com/b", "title": "Ok B"},
    ]
    links = _iter_source_links(sources, limit=10)
    assert links == [("https://ok.com/a", "Ok A"), ("https://ok.com/b", "Ok B")]


def test_iter_source_links_falls_back_to_url_when_title_empty():
    """title 为空 / 缺失 → 回退到 url。"""
    sources = [
        {"url": "https://ok.com/c", "title": ""},
        {"url": "https://ok.com/d"},
    ]
    links = _iter_source_links(sources, limit=10)
    assert links[0] == ("https://ok.com/c", "https://ok.com/c")
    assert links[1] == ("https://ok.com/d", "https://ok.com/d")


def test_iter_source_links_respects_limit():
    """limit 截断结果。"""
    sources = [{"url": f"https://ok.com/{i}", "title": str(i)} for i in range(5)]
    assert len(_iter_source_links(sources, limit=3)) == 3


def test_plain_excerpt_zero_returns_empty():
    """max_chars=0 → 空串。"""
    assert _plain_excerpt("anything", 0) == ""


def test_plain_excerpt_strips_citations_code_links_headings():
    """[cite:...] / `code` / [text](url) / '# heading' 都被剥离。"""
    raw = "## Heading\n[cite:abc]`code`[label](https://x.com) plain text"
    excerpt = _plain_excerpt(raw, 200)
    assert "[cite:" not in excerpt
    assert "`" not in excerpt
    assert "](https" not in excerpt
    assert "##" not in excerpt
    assert "plain text" in excerpt


def test_plain_excerpt_short_text_returned_as_is():
    """短文本不截断。"""
    assert _plain_excerpt("short", 100) == "short"


def test_plain_excerpt_long_text_truncated_with_ellipsis():
    """长文本截断并以 '…' 结尾。"""
    text = "x" * 50
    out = _plain_excerpt(text, 10)
    assert len(out) == 10
    assert out.endswith("…")


def test_plain_excerpt_one_char_returns_ellipsis():
    """max_chars=1 → '…'。"""
    assert _plain_excerpt("long text", 1) == "…"


def test_title_from_markdown_extracts_h1():
    """_title_from_markdown 返回首个 '# ' 行内容。"""
    assert _title_from_markdown("body\n# My Title\nmore") == "My Title"


def test_title_from_markdown_returns_empty_when_no_h1():
    assert _title_from_markdown("## No H1") == ""


def test_safe_filename_replaces_unsafe_chars():
    assert _safe_filename("hello world/foo") == "hello-world-foo"


def test_safe_filename_strips_leading_trailing_dashes():
    assert _safe_filename("---hello---") == "hello"


def test_safe_filename_empty_after_strip_returns_empty():
    assert _safe_filename("///") == ""


# ---------------------------------------------------------------------------
# B. _render_research_artifact 分发
# ---------------------------------------------------------------------------


def test_render_research_artifact_dispatches_webpage():
    content, ext = _render_research_artifact(
        "webpage", "Title", _long_report(), [{"url": "https://x.com", "title": "X"}],
    )
    assert ext == "html"
    assert "<!doctype html>" in content
    assert "Title" in content


def test_render_research_artifact_dispatches_infographic():
    content, ext = _render_research_artifact(
        "infographic", "Title", _long_report(), [],
    )
    assert ext == "html"
    assert "Infographic" in content


def test_render_research_artifact_dispatches_quiz():
    content, ext = _render_research_artifact(
        "quiz", "Title", _long_report(), [],
    )
    assert ext == "md"
    assert content.startswith("# 测验：Title")
    assert "Q1." in content


def test_render_research_artifact_dispatches_flashcards():
    content, ext = _render_research_artifact(
        "flashcards", "Title", _long_report(), [],
    )
    assert ext == "md"
    assert content.startswith("# 抽认卡：Title")
    assert "Front:" in content


def test_render_research_artifact_dispatches_audio_overview():
    content, ext = _render_research_artifact(
        "audio_overview", "Title", _long_report(), [],
    )
    assert ext == "md"
    assert content.startswith("# 音频概览脚本：Title")
    assert "Host A:" in content


def test_render_research_artifact_dispatches_custom_app():
    content, ext = _render_research_artifact(
        "custom_app", "Title", _long_report(), [],
    )
    assert ext == "md"
    assert content.startswith("# 自定义应用规格：Title")
    assert "Suggested App" in content


def test_render_research_artifact_unknown_type_raises():
    """未支持的 artifact_type → ValueError。"""
    with pytest.raises(ValueError, match="Unsupported artifact_type"):
        _render_research_artifact("unknown", "T", "body", [])


def test_render_report_webpage_uses_sections_when_available():
    """有 section → 渲染前 6 个 section 为 <section>。"""
    from src.tools.research import _render_report_webpage
    report = "## S1\nbody1\n## S2\nbody2"
    html_out = _render_report_webpage("T", report, [], _markdown_sections(report))
    assert "<section>" in html_out
    assert "S1" in html_out
    assert "S2" in html_out


def test_render_report_webpage_falls_back_when_no_sections():
    """无 section → 用单个 'Research takeaways' section 回退。"""
    from src.tools.research import _render_report_webpage
    html_out = _render_report_webpage("T", "no headings here", [], [])
    assert "Research takeaways" in html_out


def test_render_infographic_falls_back_when_no_sections():
    """无 section → 用 title 作为单卡片。"""
    from src.tools.research import _render_infographic
    out = _render_infographic("Title Only", "no headings", [], [])
    assert "Title Only" in out


# ---------------------------------------------------------------------------
# C. 报告动作 / artifact payload
# ---------------------------------------------------------------------------


def test_research_report_actions_payload_uses_report_title_when_present():
    """report_output.title 存在 → payload.title 用它。"""
    report_output = SimpleNamespace(title="My Report", report="body", immersive_id="im1")
    payload = _research_report_actions_payload(report_output, chat_id="c1")
    assert payload["title"] == "My Report"
    assert payload["immersive_id"] == "im1"
    assert payload["native_web_menu_observed"] is True
    assert len(payload["actions"]) == 6


def test_research_report_actions_payload_falls_back_to_markdown_title():
    """report_output.title 为空 → 从 report markdown 提取 H1。"""
    report_output = SimpleNamespace(title="", report="# Markdown Title\nbody", immersive_id="")
    payload = _research_report_actions_payload(report_output, chat_id="c1")
    assert payload["title"] == "Markdown Title"


def test_format_research_report_actions_renders_full_markdown():
    """_format_research_report_actions 输出含标题 / 沉浸式 ID / 动作列表 / 原生菜单注释。"""
    report_output = SimpleNamespace(title="T", report="r", immersive_id="imX")
    payload = _research_report_actions_payload(report_output, chat_id="c1")
    md = _format_research_report_actions(payload)
    assert "## Deep Research 报告创建动作" in md
    assert "标题: T" in md
    assert "沉浸式报告 ID: imX" in md
    assert "`webpage`:" in md
    assert "2026-06-21" in md  # native_web_menu_note


def test_create_research_report_artifact_writes_file_and_returns_metadata(tmp_path):
    """_create_research_report_artifact 写文件并返回元数据 dict。"""
    report_output = SimpleNamespace(
        title="My Report", report="## S1\nbody", sources=[{"url": "https://x.com", "title": "X"}],
        text="## S1\nbody",
    )
    out_dir = tmp_path / "artifacts"
    artifact = _create_research_report_artifact(
        report_output, artifact_type="quiz", chat_id="chat-1", output_dir=str(out_dir),
    )
    assert artifact["artifact_type"] == "quiz"
    assert artifact["title"] == "My Report"
    assert artifact["chat_id"] == "chat-1"
    assert artifact["source_count"] == 1
    assert os.path.isfile(artifact["path"])
    assert artifact["path"].endswith(".md")
    assert artifact["bytes"] > 0


def test_create_research_report_artifact_falls_back_to_text_when_no_report(tmp_path):
    """report_output.report 为空 → 用 .text 回退。"""
    report_output = SimpleNamespace(title="T", report="", sources=[], text="## S1\nfrom text")
    artifact = _create_research_report_artifact(
        report_output, artifact_type="webpage", chat_id="c1", output_dir=str(tmp_path),
    )
    assert artifact["artifact_type"] == "webpage"
    with open(artifact["path"], "r", encoding="utf-8") as f:
        content = f.read()
    assert "from text" in content


def test_format_research_report_artifact_renders_markdown():
    artifact = {
        "artifact_type": "quiz",
        "title": "T",
        "path": "/tmp/x.md",
        "source_count": 3,
        "native_web_menu_observed": True,
        "native_web_menu_note": "note text",
    }
    md = _format_research_report_artifact(artifact)
    assert "## Research Report Artifact Created" in md
    assert "类型: `quiz`" in md
    assert "标题: T" in md
    assert "文件: /tmp/x.md" in md
    assert "来源数: 3" in md
    assert "RPC 已观测: 是" in md
    assert "note text" in md


# ---------------------------------------------------------------------------
# D. 沉浸式报告提取（_walk_nested_json / _extract_sources_from_node /
#    _extract_deep_research_immersive_report）
# ---------------------------------------------------------------------------


def test_walk_nested_json_yields_dict_list_and_json_string():
    """dict / list / JSON-string 都被递归遍历。"""
    payload = {
        "a": ["nested", "list"],
        "b": '{"json_key": "json_val"}',  # JSON-string
        "c": "not-json-string",  # 非 JSON-string，跳过
    }
    nodes = list(_walk_nested_json(payload))
    paths = [p for p, _ in nodes]
    # 顶层 + dict 各 value + list 各 item + JSON-string 解析后的 dict value
    assert () in paths
    assert ("a",) in paths
    assert ("a", 0) in paths
    assert ("a", 1) in paths
    assert ("b",) in paths
    assert ("b", "json", "json_key") in paths
    # 非 JSON 字符串仅 yield 自身，不递归
    assert ("c",) in paths


def test_walk_nested_json_skips_non_json_string_silently():
    """字符串首字符非 [/{ → 不递归。"""
    nodes = list(_walk_nested_json("plain text [not json"))
    assert len(nodes) == 1  # 仅 yield 自身


def test_walk_nested_json_skips_invalid_json_string_starting_with_bracket():
    """字符串以 [ 或 { 开头但非合法 JSON → 走 except 分支仅 yield 自身（lines 603-605）。"""
    nodes = list(_walk_nested_json("[not valid json"))
    assert len(nodes) == 1  # 仅 yield 自身，不递归
    assert nodes[0][1] == "[not valid json"

    nodes2 = list(_walk_nested_json("{also not json"))
    assert len(nodes2) == 1


def test_extract_sources_from_node_filters_gstatic_googleusercontent_dup_and_non_str():
    """过滤 gstatic/favicon / googleusercontent / 重复 url / 非字符串字段 / 非 http url。"""
    node = [
        "immersive-id", "ignored", "title", "ignored", "report",
        [
            ["fav", "https://example.com/p1", "Page 1"],
            ["fav", "https://example.com/p1", "Dup"],  # 重复 url
            ["fav", "https://gstatic.com/favicon?x", "GS"],  # gstatic
            ["fav", "https://googleusercontent.com/y", "GW"],  # googleusercontent
            ["fav", "not-url", "Bad"],  # 非 http
            [1, "https://int-fav.com", "BadFav"],  # favicon 非 str
            ["fav", "https://example.com/p2", "Page 2"],
        ],
    ]
    sources = _extract_sources_from_node(node)
    assert sources == [
        {"url": "https://example.com/p1", "title": "Page 1"},
        {"url": "https://example.com/p2", "title": "Page 2"},
    ]


def test_extract_sources_from_node_returns_empty_when_no_valid_items():
    """无有效 source → 空列表。"""
    assert _extract_sources_from_node(["just", "strings", "here"]) == []


def test_extract_deep_research_immersive_report_returns_none_when_no_match():
    """无任何 node[4] 满足报告阈值 → None。"""
    payload = [["id", "x", "t", "y", "short"]]  # 报告 < 1000 字符
    assert _extract_deep_research_immersive_report(payload) is None


def test_extract_deep_research_immersive_report_skips_report_without_cite_or_heading():
    """报告 ≥1000 字符但无 '[cite:' 或 '##' → 跳过。"""
    long_text = "x" * 1100  # 无 cite 也无 ##
    payload = [["id", "x", "t", "y", long_text]]
    assert _extract_deep_research_immersive_report(payload) is None


def test_extract_deep_research_immersive_report_picks_longest_when_multiple():
    """多候选 → 取最长报告。"""
    short_report = "## A\n" + ("x" * 1100)
    long_report = "## B\n" + ("y" * 1500)
    payload = [
        ["id1", "x", "T1", "y", short_report],
        ["id2", "x", "T2", "y", long_report],
    ]
    extracted = _extract_deep_research_immersive_report(payload)
    assert extracted is not None
    assert extracted["report"] == long_report
    assert extracted["title"] == "T2"
    assert extracted["immersive_id"] == "id2"


def test_extract_deep_research_immersive_report_falls_back_to_markdown_title():
    """node[2] 非 str → 从报告 markdown 提取 H1 标题。"""
    report = "# My H1 Title\n## A\n" + ("x" * 1100)
    payload = [["id", "x", 123, "y", report]]  # node[2] 非 str
    extracted = _extract_deep_research_immersive_report(payload)
    assert extracted is not None
    assert extracted["title"] == "My H1 Title"
    assert extracted["immersive_id"] == "id"


def test_extract_deep_research_immersive_report_immersive_id_empty_when_node0_not_str():
    """node[0] 非 str → immersive_id 为空串。"""
    report = "## A\n" + ("x" * 1100)
    payload = [[123, "x", "T", "y", report]]
    extracted = _extract_deep_research_immersive_report(payload)
    assert extracted is not None
    assert extracted["immersive_id"] == ""


def test_extract_deep_research_immersive_report_walks_json_string_payload():
    """payload 是 JSON 字符串 → 自动解析并遍历。"""
    report = "## A\n" + ("x" * 1100)
    inner_payload = [["id", "x", "T", "y", report]]
    payload_str = json.dumps(inner_payload)
    extracted = _extract_deep_research_immersive_report(payload_str)
    assert extracted is not None
    assert extracted["immersive_id"] == "id"


# ---------------------------------------------------------------------------
# E. _fetch_deep_research_immersive_report（async）
# ---------------------------------------------------------------------------


def test_fetch_immersive_report_returns_none_when_cid_empty():
    """cid 为空串 → 立即返回 None。"""
    client = MagicMock()
    assert _run(_fetch_deep_research_immersive_report(client, "")) is None


def test_fetch_immersive_report_returns_none_when_client_has_no_batch_execute():
    """client 无 _batch_execute 属性 → None。"""
    client = SimpleNamespace()  # 无 _batch_execute
    assert _run(_fetch_deep_research_immersive_report(client, "cid-1")) is None


def test_fetch_immersive_report_returns_none_when_import_error(monkeypatch):
    """from gemini_webapi.constants import GRPC 失败 → 走 except ImportError 返回 None。

    通过 monkeypatch.delattr 删除 GRPC，让 `from gemini_webapi.constants import GRPC`
    抛 ImportError（Python 对 from-import 缺失属性抛 ImportError）。
    """
    import gemini_webapi.constants as gw_constants

    monkeypatch.delattr(gw_constants, "GRPC", raising=False)

    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = _run(_fetch_deep_research_immersive_report(client, "cid-1"))
    assert result is None


def test_fetch_immersive_report_returns_none_when_batch_execute_raises(monkeypatch):
    """_batch_execute 抛异常 → 返回 None。"""
    client = MagicMock()
    client._batch_execute = AsyncMock(side_effect=RuntimeError("network"))
    # extract_json_from_response 不应被调用
    result = _run(_fetch_deep_research_immersive_report(client, "cid-1"))
    assert result is None


def test_fetch_immersive_report_returns_none_when_no_report_extracted(monkeypatch):
    """batch_execute 成功但解析后无报告 → None。"""
    import gemini_webapi.utils as gw_utils

    client = MagicMock()
    response = SimpleNamespace(text="raw text")
    client._batch_execute = AsyncMock(return_value=response)
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: [])

    result = _run(_fetch_deep_research_immersive_report(client, "cid-1"))
    assert result is None


def test_fetch_immersive_report_happy_path_with_sources(monkeypatch):
    """有 sources → text 末尾追加 '## 来源链接' 与编号列表。"""
    import gemini_webapi.utils as gw_utils

    report = _long_report()
    inner_payload = [[
        "im-id", "ignored", "Report Title", "ignored", report,
        [["fav", "https://example.com/p1", "Page 1"]],
    ]]
    client = MagicMock()
    response = SimpleNamespace(text="raw")
    client._batch_execute = AsyncMock(return_value=response)
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: inner_payload)

    result = _run(_fetch_deep_research_immersive_report(client, "cid-1"))
    assert result is not None
    assert result.title == "Report Title"
    assert result.immersive_id == "im-id"
    assert result.native_web_menu_observed is False
    assert result.sources == [{"url": "https://example.com/p1", "title": "Page 1"}]
    assert "## 来源链接" in result.text
    assert "1. [Page 1](https://example.com/p1)" in result.text
    assert result.report == report.strip()


def test_fetch_immersive_report_happy_path_without_sources(monkeypatch):
    """无 sources → text 仅含 report，无 '## 来源链接'。"""
    import gemini_webapi.utils as gw_utils

    report = _long_report()
    inner_payload = [["im-id", "ignored", "T", "ignored", report]]
    client = MagicMock()
    response = SimpleNamespace(text="raw")
    client._batch_execute = AsyncMock(return_value=response)
    monkeypatch.setattr(gw_utils, "extract_json_from_response", lambda text: inner_payload)

    result = _run(_fetch_deep_research_immersive_report(client, "cid-1"))
    assert result is not None
    assert result.sources == []
    assert "## 来源链接" not in result.text


# ---------------------------------------------------------------------------
# F. _request_completed_research_report（async）
# ---------------------------------------------------------------------------


def test_request_completed_research_report_returns_none_when_no_send_message():
    """chat 无 send_message 属性 → None。"""
    chat = SimpleNamespace()  # 无 send_message
    assert _run(_request_completed_research_report(chat)) is None


def test_request_completed_research_report_returns_response_on_happy_path():
    """chat.send_message 成功 → 返回其响应。"""
    expected = SimpleNamespace(text="report body")
    chat = SimpleNamespace(send_message=AsyncMock(return_value=expected))
    result = _run(_request_completed_research_report(chat))
    assert result is expected
    chat.send_message.assert_awaited_once()


def test_request_completed_research_report_returns_none_on_exception():
    """chat.send_message 抛异常 → None。"""
    chat = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("boom")))
    assert _run(_request_completed_research_report(chat)) is None


# ---------------------------------------------------------------------------
# G. Native recovery helpers
# ---------------------------------------------------------------------------


def test_resolve_deep_research_transport_model_default_for_flash():
    """flash alias → Model.UNSPECIFIED + 'Gemini Web default' note。"""
    model, note = _resolve_deep_research_transport_model("flash")
    assert getattr(model, "model_name", None) == "unspecified"
    assert "Gemini Web default Deep Research mode" in note
    assert "flash" in note


def test_resolve_deep_research_transport_model_default_for_lite_pro_thinking_fast():
    """flash-lite / lite / pro / thinking / fast 都映射到 default transport。"""
    for alias in ("flash-lite", "lite", "pro", "thinking", "fast", "FLASH", "Pro"):
        model, note = _resolve_deep_research_transport_model(alias)
        assert getattr(model, "model_name", None) == "unspecified", alias


def test_resolve_deep_research_transport_model_passthrough_for_non_alias():
    """非标准 alias（如 gemini-3-pro 字面值）→ passthrough resolved model。"""
    model, note = _resolve_deep_research_transport_model("gemini-3-pro")
    # 返回 resolved model（字符串），note 同 model
    assert model == note
    assert "gemini-3-pro" in model


def test_resolve_deep_research_transport_model_treats_none_as_flash():
    """requested_model=None → 视为 flash。"""
    model, note = _resolve_deep_research_transport_model(None)
    assert getattr(model, "model_name", None) == "unspecified"
    assert "flash" in note


def test_resolve_deep_research_transport_model_treats_empty_as_flash():
    """requested_model='' → 视为 flash。"""
    model, note = _resolve_deep_research_transport_model("")
    assert getattr(model, "model_name", None) == "unspecified"
    assert "flash" in note


def test_resolve_deep_research_transport_model_returns_resolved_when_no_gemini_webapi(monkeypatch):
    """from gemini_webapi.constants import Model 失败 → 返回 (resolved, resolved)。"""
    import gemini_webapi.constants as gw_constants

    monkeypatch.delattr(gw_constants, "Model", raising=False)

    model, note = _resolve_deep_research_transport_model("flash")
    # ImportError 分支：两者都是 resolve_model_name("flash") 的返回
    assert model == note
    assert "flash" in model.lower()


def test_is_default_deep_research_transport_for_unspecified_model():
    """Model.UNSPECIFIED → True。"""
    from gemini_webapi.constants import Model
    assert _is_default_deep_research_transport(Model.UNSPECIFIED) is True


def test_is_default_deep_research_transport_for_unspecified_string():
    """'unspecified' 字符串 → True。"""
    assert _is_default_deep_research_transport("unspecified") is True


def test_is_default_deep_research_transport_for_other_model():
    """其他 model → False。"""
    from gemini_webapi.constants import Model
    assert _is_default_deep_research_transport(Model.PLUS_PRO) is False


def test_is_capability_probe_false_negative_matches_both_markers():
    """错误文本含 'appears not eligible for deep research' 与 'Failed: []' → True。"""
    err = RuntimeError("Account appears not eligible for deep research. Failed: []")
    assert _is_capability_probe_false_negative(err) is True


def test_is_capability_probe_false_negative_returns_false_when_missing_marker():
    """缺任一 marker → False。"""
    assert _is_capability_probe_false_negative(RuntimeError("appears not eligible for deep research")) is False
    assert _is_capability_probe_false_negative(RuntimeError("Failed: []")) is False


def test_is_research_start_message_matches_known_markers():
    """已知的'研究已启动'消息 → True。"""
    assert _is_research_start_message("I'm on it, give me a moment") is True
    assert _is_research_start_message("Research is finished, see below") is True
    assert _is_research_start_message("我这就开始研究") is True


def test_is_research_start_message_returns_false_for_unknown():
    assert _is_research_start_message("random unrelated text") is False


def test_is_research_completion_message_matches_known_markers():
    """已知的'研究完成'消息 → True。"""
    assert _is_research_completion_message("I've finished the research") is True
    assert _is_research_completion_message("我已经完成了研究") is True


def test_is_research_completion_message_returns_false_for_start_only():
    """仅 start marker 而无 completion marker → False。"""
    assert _is_research_completion_message("I'm on it") is False


# ---------------------------------------------------------------------------
# H. _create_deep_research_plan / _start_deep_research_with_recovery /
#    _start_fresh_research_chat（async）
# ---------------------------------------------------------------------------


def test_create_deep_research_plan_happy_path():
    """无异常 → 直接返回 client.create_deep_research_plan 的结果。"""
    expected_plan = SimpleNamespace(research_id="r1", title="T", cid="c1")
    client = SimpleNamespace(
        create_deep_research_plan=AsyncMock(return_value=expected_plan),
    )
    chat = SimpleNamespace(cid="c1")
    plan = _run(_create_deep_research_plan(client, "query", chat, "model"))
    assert plan is expected_plan
    client.create_deep_research_plan.assert_awaited_once_with("query", chat=chat, model="model")


def test_create_deep_research_plan_reraises_non_probe_exception():
    """非 capability probe 异常 → 直接 raise。"""
    client = SimpleNamespace(
        create_deep_research_plan=AsyncMock(side_effect=RuntimeError("network down")),
        _deep_research_preflight=AsyncMock(),
        _collect_research_output=AsyncMock(),
    )
    with pytest.raises(RuntimeError, match="network down"):
        _run(_create_deep_research_plan(client, "q", SimpleNamespace(cid="c"), "m"))


def test_create_deep_research_plan_reraises_when_client_lacks_recovery_attrs():
    """是 capability probe 异常但 client 无 _deep_research_preflight/_collect_research_output → raise。"""
    err = RuntimeError("Account appears not eligible for deep research. Failed: []")
    client = SimpleNamespace(
        create_deep_research_plan=AsyncMock(side_effect=err),
        # 缺 _deep_research_preflight / _collect_research_output
    )
    with pytest.raises(RuntimeError, match="appears not eligible"):
        _run(_create_deep_research_plan(client, "q", SimpleNamespace(cid="c"), "m"))


def test_create_deep_research_plan_recovery_when_output_has_no_plan():
    """capability probe 异常 + 有 recovery attrs + output.deep_research_plan 缺失 → raise。"""
    err = RuntimeError("Account appears not eligible for deep research. Failed: []")
    output = SimpleNamespace(text="some text", deep_research_plan=None)
    client = SimpleNamespace(
        create_deep_research_plan=AsyncMock(side_effect=err),
        _deep_research_preflight=AsyncMock(),
        _collect_research_output=AsyncMock(return_value=output),
    )
    with pytest.raises(RuntimeError, match="appears not eligible"):
        _run(_create_deep_research_plan(client, "q", SimpleNamespace(cid="c"), "m"))


def test_create_deep_research_plan_recovery_synthesizes_plan():
    """capability probe 异常 + 有 recovery attrs + output.deep_research_plan 存在 → 合成 plan。"""
    err = RuntimeError("Account appears not eligible for deep research. Failed: []")
    plan = SimpleNamespace(research_id="", title="", cid="", confirm_prompt="", response_text="")
    output = SimpleNamespace(text="output text body", deep_research_plan=plan)
    client = SimpleNamespace(
        create_deep_research_plan=AsyncMock(side_effect=err),
        _deep_research_preflight=AsyncMock(),
        _collect_research_output=AsyncMock(return_value=output),
    )
    chat = SimpleNamespace(cid="c-chat", metadata=["m1", "m2"])
    result = _run(_create_deep_research_plan(client, "query", chat, "model"))
    # metadata 复制自 chat
    assert result.metadata == ["m1", "m2"]
    # cid 来自 chat.cid
    assert result.cid == "c-chat"
    # 缺失字段被填充
    assert result.confirm_prompt == "Start research"
    assert result.response_text == "output text body"
    # preflight 与 collect 都被调用
    client._deep_research_preflight.assert_awaited_once()
    client._collect_research_output.assert_awaited_once_with(chat, "query")


def test_create_deep_research_plan_recovery_preserves_existing_plan_fields():
    """recovery 路径下 plan 已有 confirm_prompt/response_text → 不覆盖。"""
    err = RuntimeError("Account appears not eligible for deep research. Failed: []")
    plan = SimpleNamespace(
        research_id="r-x", title="T", cid="plan-cid",
        confirm_prompt="existing prompt", response_text="existing text",
    )
    output = SimpleNamespace(text="output text body", deep_research_plan=plan)
    client = SimpleNamespace(
        create_deep_research_plan=AsyncMock(side_effect=err),
        _deep_research_preflight=AsyncMock(),
        _collect_research_output=AsyncMock(return_value=output),
    )
    chat = SimpleNamespace(cid="", metadata=None)  # 空 cid → 回退 plan.cid
    result = _run(_create_deep_research_plan(client, "q", chat, "m"))
    assert result.cid == "plan-cid"
    assert result.confirm_prompt == "existing prompt"
    assert result.response_text == "existing text"
    assert result.metadata == []  # metadata=None → []


def test_start_deep_research_with_recovery_happy_path():
    """无超时 → 直接返回 client.start_deep_research 的结果。"""
    expected = SimpleNamespace(text="started")
    client = SimpleNamespace(start_deep_research=AsyncMock(return_value=expected))
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c1")
    result = _run(_start_deep_research_with_recovery(client, plan, chat, timeout=30))
    assert result is expected


def test_start_deep_research_with_recovery_timeout_falls_back_to_fetch_latest():
    """超时 + chat.cid + 有 fetch_latest_chat_response → 调它返回结果。"""
    latest = SimpleNamespace(text="latest", timeout_during_start=False)
    client = SimpleNamespace(
        start_deep_research=AsyncMock(side_effect=asyncio.TimeoutError),
        fetch_latest_chat_response=AsyncMock(return_value=latest),
    )
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c-late")
    result = _run(_start_deep_research_with_recovery(client, plan, chat, timeout=1))
    assert result is latest
    client.fetch_latest_chat_response.assert_awaited_once_with("c-late")


def test_start_deep_research_with_recovery_timeout_falls_back_to_plan_cid():
    """超时 + chat.cid 空 + plan.cid 有 → 用 plan.cid 调 fetch_latest_chat_response。"""
    latest = SimpleNamespace(text="latest")
    client = SimpleNamespace(
        start_deep_research=AsyncMock(side_effect=asyncio.TimeoutError),
        fetch_latest_chat_response=AsyncMock(return_value=latest),
    )
    plan = SimpleNamespace(cid="plan-cid")
    chat = SimpleNamespace(cid="")  # 空 cid → 回退 plan.cid
    result = _run(_start_deep_research_with_recovery(client, plan, chat, timeout=1))
    assert result is latest
    client.fetch_latest_chat_response.assert_awaited_once_with("plan-cid")


def test_start_deep_research_with_recovery_timeout_returns_empty_namespace_when_no_fetch():
    """超时 + 无 fetch_latest_chat_response attr → 返回 SimpleNamespace(text='', timeout_during_start=True)。"""
    client = SimpleNamespace(
        start_deep_research=AsyncMock(side_effect=asyncio.TimeoutError),
        # 无 fetch_latest_chat_response
    )
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c1")
    result = _run(_start_deep_research_with_recovery(client, plan, chat, timeout=1))
    assert result.text == ""
    assert result.timeout_during_start is True


def test_start_deep_research_with_recovery_timeout_returns_empty_when_fetch_returns_none():
    """超时 + fetch_latest_chat_response 返回 None → 返回空 SimpleNamespace。"""
    client = SimpleNamespace(
        start_deep_research=AsyncMock(side_effect=asyncio.TimeoutError),
        fetch_latest_chat_response=AsyncMock(return_value=None),
    )
    plan = SimpleNamespace(cid="p-cid")
    chat = SimpleNamespace(cid="")  # 空 → plan.cid
    result = _run(_start_deep_research_with_recovery(client, plan, chat, timeout=1))
    assert result.text == ""
    assert result.timeout_during_start is True


def test_start_fresh_research_chat_clears_cid_rid_rcid():
    """_start_fresh_research_chat 把 chat.cid/rid/rcid 设为 ''。"""
    chat = SimpleNamespace(cid="c", rid="r", rcid="rc", metadata=["m"])
    client = SimpleNamespace(start_chat=MagicMock(return_value=chat))
    result = _start_fresh_research_chat(client, model="m")
    assert result is chat
    assert chat.cid == ""
    assert chat.rid == ""
    assert chat.rcid == ""
    client.start_chat.assert_called_once_with(model="m")


def test_start_fresh_research_chat_swallows_attr_set_exception(monkeypatch):
    """setattr(chat, attr, '') 抛异常 → 仅 log debug，不崩溃。"""
    class _Protected:
        def __init__(self):
            self.cid = "c"
            self.rid = "r"
        @property
        def rcid(self):
            return "rc"
        @rcid.setter
        def rcid(self, v):
            raise AttributeError("read-only-ish")  # 模拟 setattr 抛异常
    chat = _Protected()
    client = SimpleNamespace(start_chat=MagicMock(return_value=chat))
    # 不应崩溃
    result = _start_fresh_research_chat(client, model="m")
    assert result is chat
    # cid 和 rid 仍被清空（在 rcid 之前）
    assert chat.cid == ""
    assert chat.rid == ""


# ---------------------------------------------------------------------------
# H2. _wait_for_deep_research_by_chat 轮询分支
# ---------------------------------------------------------------------------


def test_wait_for_deep_research_by_chat_returns_immersive_report_on_completion(monkeypatch):
    """polling 检测到 completion message + immersive report 有文本 → 返回 immersive（lines 405-421）。"""
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c1")
    start_output = SimpleNamespace(text="I'm on it, researching now")
    latest_output = SimpleNamespace(text="I've finished the research")
    client = SimpleNamespace(
        fetch_latest_chat_response=AsyncMock(return_value=latest_output),
    )

    immersive = SimpleNamespace(text="immersive report body", title="T", sources=[])
    monkeypatch.setattr(
        research_tools, "_fetch_deep_research_immersive_report",
        AsyncMock(return_value=immersive),
    )

    result = _run(_wait_for_deep_research_by_chat(
        client, plan, chat, start_output, poll_interval=1, timeout=10,
    ))
    assert result.done is True
    assert result.final_output is immersive
    assert "retrieved immersive report" in result.statuses[0].notes[0]


def test_wait_for_deep_research_by_chat_returns_followup_when_immersive_empty(monkeypatch):
    """completion message + immersive report 无文本 → 调 _request_completed_research_report → 有文本 → 返回（lines 422-437）。"""
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c1")
    start_output = SimpleNamespace(text="I'm on it, researching now")
    latest_output = SimpleNamespace(text="I've finished the research")
    client = SimpleNamespace(
        fetch_latest_chat_response=AsyncMock(return_value=latest_output),
    )

    # immersive 返回 None（无文本）
    monkeypatch.setattr(
        research_tools, "_fetch_deep_research_immersive_report",
        AsyncMock(return_value=None),
    )
    # followup 返回有文本
    followup = SimpleNamespace(text="followup report body")
    monkeypatch.setattr(
        research_tools, "_request_completed_research_report",
        AsyncMock(return_value=followup),
    )

    result = _run(_wait_for_deep_research_by_chat(
        client, plan, chat, start_output, poll_interval=1, timeout=10,
    ))
    assert result.done is True
    assert result.final_output is followup
    assert "retrieved completed report by follow-up" in result.statuses[0].notes[0]


def test_wait_for_deep_research_by_chat_skips_followup_when_start_message(monkeypatch):
    """completion message + immersive 空 + followup 是 start message → 不返回，继续轮询。"""
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c1")
    start_output = SimpleNamespace(text="I'm on it, researching now")
    # 第一次返回 completion message，第二次返回与 start 不同的非 start/completion 文本
    call_count = {"n": 0}
    completion = SimpleNamespace(text="I've finished the research")
    other = SimpleNamespace(text="Here is the actual content")
    latest_outputs = [completion, other]

    async def fake_fetch_latest(_cid):
        idx = min(call_count["n"], len(latest_outputs) - 1)
        call_count["n"] += 1
        return latest_outputs[idx]

    client = SimpleNamespace(fetch_latest_chat_response=fake_fetch_latest)

    async def fake_immersive(_client, _cid):
        return None  # 总是空

    monkeypatch.setattr(research_tools, "_fetch_deep_research_immersive_report", fake_immersive)

    # followup 返回 start message（不应被采纳）
    async def fake_followup(_chat):
        return SimpleNamespace(text="I'm on it, still researching")

    monkeypatch.setattr(research_tools, "_request_completed_research_report", fake_followup)

    result = _run(_wait_for_deep_research_by_chat(
        client, plan, chat, start_output, poll_interval=1, timeout=10,
    ))
    # 第二次轮询：latest_text="Here is the actual content" 与 start_text 不同 + 非 start message → 返回
    assert result.done is True
    assert result.final_output is other
    assert "chat history produced final output" in result.statuses[0].notes[0]


def test_wait_for_deep_research_by_chat_returns_latest_when_text_differs_from_start(monkeypatch):
    """latest_text 与 start_text 不同 + 非 start message → 返回 latest_output（lines 438-451）。"""
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="c1")
    start_output = SimpleNamespace(text="I'm on it, researching now")
    latest_output = SimpleNamespace(text="Here is the actual content")
    client = SimpleNamespace(
        fetch_latest_chat_response=AsyncMock(return_value=latest_output),
    )

    result = _run(_wait_for_deep_research_by_chat(
        client, plan, chat, start_output, poll_interval=1, timeout=10,
    ))
    assert result.done is True
    assert result.final_output is latest_output
    assert "chat history produced final output" in result.statuses[0].notes[0]


def test_wait_for_deep_research_by_chat_returns_running_status_on_timeout(monkeypatch):
    """无 cid → 跳过 if 块，循环空转至 timeout → 返回 done=False（lines 455-472）。"""
    plan = SimpleNamespace()
    chat = SimpleNamespace(cid="")  # 无 cid → 跳过 if 块
    start_output = SimpleNamespace(text="start")
    client = SimpleNamespace()  # 无 fetch_latest_chat_response

    # No-op sleep 避免真实等待
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    result = _run(_wait_for_deep_research_by_chat(
        client, plan, chat, start_output, poll_interval=0, timeout=0.001,
    ))
    assert result.done is False
    assert result.final_output is None
    assert "research_id was not present" in result.statuses[0].notes[0]
    assert result.statuses[0].state == "running"


# ---------------------------------------------------------------------------
# H3. _render_audio_overview with sources（覆盖 line 956 source 列表渲染）
# ---------------------------------------------------------------------------


def test_render_audio_overview_renders_source_trail_when_sources_present():
    """有 sources → 'Source Trail' 段渲染编号链接（line 956）。"""
    from src.tools.research import _render_audio_overview
    sources = [
        {"url": "https://example.com/p1", "title": "P1"},
        {"url": "https://example.com/p2", "title": "P2"},
    ]
    out = _render_audio_overview("Title", _long_report(), sources, _markdown_sections(_long_report()))
    assert "## Source Trail" in out
    assert "1. [P1](https://example.com/p1)" in out
    assert "2. [P2](https://example.com/p2)" in out


# ---------------------------------------------------------------------------
# I. _format_deep_research_result 边界
# ---------------------------------------------------------------------------


def test_format_deep_research_result_clears_report_when_not_done_and_start_message():
    """done=False + final_output.text 是 start message → 清空，走 plan_text 回退分支。"""
    plan = SimpleNamespace(
        research_id="r1", title="T", cid="c1",
        response_text="plan response text",
    )
    result = SimpleNamespace(
        plan=plan,
        final_output=SimpleNamespace(text="I'm on it, researching now"),
        start_output=SimpleNamespace(text="start text"),
        statuses=[SimpleNamespace(state="running", done=False, notes=["pending"])],
        done=False,
    )
    tc = _format_deep_research_result("query", result, requested_model="flash",
                                       research_model="m", model_note="note")
    # 报告被清空，回退到 plan.response_text
    assert "## 报告" not in tc.text
    assert "## 当前结果" in tc.text
    assert "plan response text" in tc.text
    # not done → 加超时提示
    assert "研究已启动，但超时时间内还没有可读取的最终报告" in tc.text


def test_format_deep_research_result_uses_start_text_when_no_response_text():
    """done=False + plan.response_text 空 → 用 start_output.text 回退。"""
    plan = SimpleNamespace(
        research_id="", title="", cid="c1", response_text="",
    )
    result = SimpleNamespace(
        plan=plan,
        final_output=None,
        start_output=SimpleNamespace(text="start text only"),
        statuses=[],
        done=False,
    )
    tc = _format_deep_research_result("q", result)
    assert "start text only" in tc.text
    assert "研究已启动，但超时时间内还没有可读取的最终报告" in tc.text


def test_format_deep_research_result_done_with_no_report_uses_plan_text_without_prefix():
    """done=True + final_output 空 → 用 plan_text 但不加超时前缀。"""
    plan = SimpleNamespace(
        research_id="", title="", cid="c1",
        response_text="plan response only",
    )
    result = SimpleNamespace(
        plan=plan,
        final_output=None,
        start_output=SimpleNamespace(text="start text"),
        statuses=[],
        done=True,
    )
    tc = _format_deep_research_result("q", result)
    assert "plan response only" in tc.text
    # done=True → 不加超时前缀
    assert "研究已启动，但超时" not in tc.text
    assert "## 当前结果" in tc.text


def test_format_deep_research_result_skips_status_when_no_statuses():
    """statuses 为空 → 不渲染 '最新状态' / '最新进度' 行。"""
    plan = SimpleNamespace(research_id="", title="", cid="c1", response_text="r")
    result = SimpleNamespace(
        plan=plan, final_output=None, start_output=None, statuses=[], done=True,
    )
    tc = _format_deep_research_result("q", result)
    assert "最新状态" not in tc.text


# ---------------------------------------------------------------------------
# J. 入口工具：gemini_list_research_report_actions / gemini_create_from_research_report
# ---------------------------------------------------------------------------


def _patch_entry_env(monkeypatch, *, report_output=None):
    """统一 patch 入口工具的外部接缝：get_gemini_client / initialize_client /
    _fetch_deep_research_immersive_report。
    """
    client = SimpleNamespace()
    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None
    monkeypatch.setattr(research_tools, "initialize_client", fake_init)

    async def fake_fetch(_client, _cid):
        return report_output
    monkeypatch.setattr(research_tools, "_fetch_deep_research_immersive_report", fake_fetch)
    return client


def _make_mcp():
    mcp = FastMCP("test")
    research_tools.register_research_tools(mcp)
    return mcp


async def _call_tool(mcp, name, **kwargs):
    content, _structured = await mcp.call_tool(name, kwargs)
    return content


def test_list_research_report_actions_returns_error_when_no_report(monkeypatch):
    """_fetch_deep_research_immersive_report 返回 None → 错误文本。"""
    _patch_entry_env(monkeypatch, report_output=None)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(mcp, "gemini_list_research_report_actions", chat_id="c1"))
    assert len(result) == 1
    assert "❌ 未能在聊天 c1 中读取 Deep Research 沉浸式报告" in result[0].text


def test_list_research_report_actions_returns_markdown_by_default(monkeypatch):
    """默认 response_format='markdown' → 渲染 markdown。"""
    report_output = SimpleNamespace(
        title="My Title", report="r", immersive_id="im-1",
        text="r", sources=[],
    )
    _patch_entry_env(monkeypatch, report_output=report_output)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(mcp, "gemini_list_research_report_actions", chat_id="c1"))
    assert "## Deep Research 报告创建动作" in result[0].text
    assert "My Title" in result[0].text
    assert "im-1" in result[0].text
    assert "`webpage`:" in result[0].text


def test_list_research_report_actions_returns_json_when_requested(monkeypatch):
    """response_format='json' → 返回 JSON payload。"""
    report_output = SimpleNamespace(
        title="JSON Title", report="r", immersive_id="im-json", text="r", sources=[],
    )
    _patch_entry_env(monkeypatch, report_output=report_output)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(
        mcp, "gemini_list_research_report_actions",
        chat_id="c-json", response_format="json",
    ))
    payload = json.loads(result[0].text)
    assert payload["chat_id"] == "c-json"
    assert payload["title"] == "JSON Title"
    assert payload["immersive_id"] == "im-json"
    assert payload["native_web_menu_observed"] is True
    assert len(payload["actions"]) == 6


def test_create_from_research_report_returns_error_when_no_report(monkeypatch):
    """_fetch_deep_research_immersive_report 返回 None → 错误文本。"""
    _patch_entry_env(monkeypatch, report_output=None)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(
        mcp, "gemini_create_from_research_report", chat_id="c1",
    ))
    assert "❌ 未能在聊天 c1 中读取 Deep Research 沉浸式报告" in result[0].text


def test_create_from_research_report_returns_markdown_by_default(monkeypatch, tmp_path):
    """默认 response_format='markdown' → 渲染 artifact markdown。"""
    report_output = SimpleNamespace(
        title="Artifact Title", report="## S1\n" + ("x" * 1100),
        immersive_id="im-1", text="## S1\n" + ("x" * 1100), sources=[],
    )
    _patch_entry_env(monkeypatch, report_output=report_output)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(
        mcp, "gemini_create_from_research_report",
        chat_id="c1", output_dir=str(tmp_path),
    ))
    assert "## Research Report Artifact Created" in result[0].text
    assert "Artifact Title" in result[0].text


def test_create_from_research_report_returns_json_when_requested(monkeypatch, tmp_path):
    """response_format='json' → 返回 JSON artifact。"""
    report_output = SimpleNamespace(
        title="T", report="## S1\n" + ("x" * 1100),
        immersive_id="im", text="## S1\n" + ("x" * 1100), sources=[],
    )
    _patch_entry_env(monkeypatch, report_output=report_output)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(
        mcp, "gemini_create_from_research_report",
        chat_id="c1", output_dir=str(tmp_path), response_format="json",
    ))
    artifact = json.loads(result[0].text)
    assert artifact["artifact_type"] == "webpage"  # 默认
    assert artifact["title"] == "T"
    assert artifact["chat_id"] == "c1"
    assert "path" in artifact


@pytest.mark.parametrize("artifact_type,expected_ext", [
    ("webpage", "html"),
    ("infographic", "html"),
    ("quiz", "md"),
    ("flashcards", "md"),
    ("audio_overview", "md"),
    ("custom_app", "md"),
])
def test_create_from_research_report_dispatches_all_artifact_types(
    monkeypatch, tmp_path, artifact_type, expected_ext,
):
    """6 种 artifact_type 都被分发到对应 renderer，生成正确扩展名。"""
    report_output = SimpleNamespace(
        title="T", report="## S1\n" + ("x" * 1100),
        immersive_id="im", text="## S1\n" + ("x" * 1100), sources=[],
    )
    _patch_entry_env(monkeypatch, report_output=report_output)
    mcp = _make_mcp()
    result = asyncio.run(_call_tool(
        mcp, "gemini_create_from_research_report",
        chat_id="c1", artifact_type=artifact_type,
        output_dir=str(tmp_path), response_format="json",
    ))
    artifact = json.loads(result[0].text)
    assert artifact["artifact_type"] == artifact_type
    assert artifact["path"].endswith(f".{expected_ext}")


# ---------------------------------------------------------------------------
# K. gemini_deep_research 走 _wait_for_deep_research_by_chat 分支
#    （plan.research_id 缺失 → 不调 wait_for_deep_research，line 90）
# ---------------------------------------------------------------------------


class _NativeClientWithoutResearchId:
    """有 native API 但 plan.research_id 为空 → 走 _wait_for_deep_research_by_chat。"""

    def __init__(self, *, plan_cid="c_plan"):
        self._plan_cid = plan_cid
        self.captured_plan_query = None
        self.captured_start_plan = None
        self.wait_for_deep_research_called = False

    def start_chat(self, model=None):
        return SimpleNamespace(cid="", rid="", rcid="", metadata=[])

    async def create_deep_research_plan(self, query, chat=None, model=None):
        self.captured_plan_query = query
        return SimpleNamespace(
            research_id="",  # 关键：空 research_id 触发 _wait_for_deep_research_by_chat
            title="T", response_text="plan text", confirm_prompt="start",
            cid=self._plan_cid,
        )

    async def start_deep_research(self, plan, chat=None):
        self.captured_start_plan = plan
        return SimpleNamespace(text="started")

    async def wait_for_deep_research(self, plan, poll_interval=None, timeout=None):
        # 不应被调用
        self.wait_for_deep_research_called = True
        raise AssertionError("wait_for_deep_research should not be called when research_id is empty")


def test_deep_research_uses_wait_for_deep_research_by_chat_when_plan_lacks_research_id(monkeypatch):
    """plan.research_id 为空 → 走 _wait_for_deep_research_by_chat 分支（line 90）。"""
    client = _NativeClientWithoutResearchId(plan_cid="c-plan-xyz")

    # patch 外部接缝
    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: client)

    async def fake_init():
        return None
    monkeypatch.setattr(research_tools, "initialize_client", fake_init)

    async def fake_cleanup(_client):
        return None
    monkeypatch.setattr(research_tools, "cleanup_due_remote_chats", fake_cleanup)

    schedule_calls = []
    monkeypatch.setattr(
        research_tools, "schedule_remote_chat_cleanup",
        lambda cid, *, retain_chat, delete_after_seconds, source: schedule_calls.append({
            "cid": cid, "retain_chat": retain_chat,
            "delete_after_seconds": delete_after_seconds, "source": source,
        }),
    )

    # 替身 _wait_for_deep_research_by_chat 验证它被调用且返回值被采用
    expected_result = SimpleNamespace(
        plan=SimpleNamespace(cid="c-plan-xyz"),
        final_output=SimpleNamespace(text="chat-history report"),
        start_output=SimpleNamespace(text="started"),
        statuses=[SimpleNamespace(state="completed", done=True, notes=["ok"])],
        done=True,
    )
    wait_by_chat_calls = []

    async def fake_wait_by_chat(*, client, plan, chat, start_output, poll_interval, timeout):
        wait_by_chat_calls.append({
            "client": client, "plan": plan, "chat": chat,
            "start_output": start_output, "poll_interval": poll_interval,
            "timeout": timeout,
        })
        return expected_result

    monkeypatch.setattr(research_tools, "_wait_for_deep_research_by_chat", fake_wait_by_chat)

    mcp = _make_mcp()
    result = asyncio.run(_call_tool(
        mcp, "gemini_deep_research",
        query="q", timeout_seconds=30, poll_interval_seconds=5,
    ))

    # _wait_for_deep_research_by_chat 被调用
    assert len(wait_by_chat_calls) == 1
    assert wait_by_chat_calls[0]["client"] is client
    assert wait_by_chat_calls[0]["poll_interval"] == 5
    assert wait_by_chat_calls[0]["timeout"] == 30
    # wait_for_deep_research 未被调用
    assert client.wait_for_deep_research_called is False
    # 返回文本含 _format_deep_research_result 处理后的内容
    assert "chat-history report" in result[0].text
    # schedule cleanup 用 plan.cid（chat.cid 被 _start_fresh_research_chat 清空为 ''）
    assert schedule_calls[0]["cid"] == "c-plan-xyz"
    assert schedule_calls[0]["source"] == "gemini_deep_research"
