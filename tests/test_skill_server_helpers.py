"""skill_server 的 helper / PromptManager / 异步工具分支覆盖测试。

调研发现 skill_server.py 此前仅 prompts/cookie 两个工具有功能测试（且用
_FakePromptManager 替身跳过真实 PromptManager）。147 miss 集中在：
- PromptManager 全套（`__init__`/`_load`/`_save`/`list_all`/`get_by_name`/
  `create`/`delete`）—— 零直接覆盖
- `_format_response` 的 images/videos/audio_url 分支 —— 零直接覆盖
- `_truncate_text` / `_error_text` 小工具 —— 零直接覆盖
- `_ensure_config_dir` / `_init_default_prompts` / `get_prompts` 单例 —— 零直接覆盖
- `chat` / `history` / `account` 子处理器 / `scheduled` 子处理器 / `create` /
  `edit` / `session` / `doctor` / `cleanup` 的早退与顶层 except 分支 —— 零覆盖

mock 边界：
- client_wrapper 接缝：`get_gemini_client` / `initialize_client` /
  `cleanup_due_remote_chats` / `reset_client` / `get_cookie_status` /
  `get_cookie_from_browser` / `list_browser_cookie_profiles` /
  `schedule_remote_chat_cleanup` / `schedule_remote_chat_cleanup_from_response`
- tools.manage 接缝：`_fetch_scheduled_registry` / `_fetch_scheduled_task_by_id` /
  `_read_chat_turns` / `_chat_to_dict` / `_paginate_items` / `_extract_rpc_bodies` /
  `_summarize_probe_response` / `_fetch_native_notebooks` /
  `_cleanup_test_artifacts_payload` / `_doctor_payload` /
  `_format_doctor_markdown` / `_format_cleanup_markdown` /
  `_format_chat_export_markdown` / `_format_web_capabilities_markdown` /
  `_format_tool_manifest_markdown` / `_tool_manifest_payload` /
  `_web_capabilities_payload` / `_scheduled_daily_payload` /
  `_parse_scheduled_action_create_body` / `_parse_public_link_entry` /
  `_parse_usage_entry` / `_parse_library_capability` /
  `_parse_scheduled_action_task_entry` / `_parse_tool_mode_entry` /
  `_get_probe` / `_execute_observed_rpc`
- 模块全局：`CONFIG_DIR` / `PROMPTS_FILE` / `DEFAULT_PROMPTS_FILE` /
  `_prompt_manager` / `_sessions`
- constants：`resolve_model_name` / `resolve_media_request`
- tools.utils：`validate_optional_image_path` / `extract_remote_chat_id`
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import src.skill_server as skill_server
from src.skill_server import (
    PromptManager,
    _account_features,
    _account_library,
    _account_links,
    _account_models,
    _account_modes,
    _account_notebooks,
    _account_scheduled,
    _account_status,
    _account_usage,
    _ensure_config_dir,
    _error_text,
    _format_response,
    _init_default_prompts,
    _normalize_media_type,
    _normalize_model,
    _scheduled_create,
    _scheduled_delete,
    _scheduled_get,
    _scheduled_list,
    _session_send,
    _truncate_text,
    get_prompts,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A. 纯 helper（_truncate_text / _error_text / _normalize_model / _normalize_media_type）
# ---------------------------------------------------------------------------


def test_truncate_text_returns_original_when_under_limit():
    assert _truncate_text("short", 2000) == "short"


def test_truncate_text_returns_empty_for_falsy_input():
    assert _truncate_text(None, 2000) == ""
    assert _truncate_text("", 2000) == ""


def test_truncate_text_truncates_and_appends_marker():
    long_text = "x" * 3000
    result = _truncate_text(long_text, max_chars=100)
    assert result.endswith("\n...[truncated]")
    # 100 chars (rstrip) + "\n...[truncated]" (15 chars)
    assert len(result) <= 115


def test_truncate_text_strips_trailing_whitespace_before_marker():
    long_text = "x " * 100  # 200 chars, ends with space
    result = _truncate_text(long_text, max_chars=50)
    assert result.endswith("\n...[truncated]")
    # rstrip() removes the trailing space before the marker
    assert "...[truncated]" in result


def test_error_text_logs_and_returns_textcontent():
    err = ValueError("boom")
    result = _error_text(err, "Chat")
    assert len(result) == 1
    assert result[0].text == "Error: boom"


def test_normalize_model_resolves_short_aliases():
    assert _normalize_model("f") == "flash"
    assert _normalize_model("t") == "thinking"
    assert _normalize_model("p") == "pro"
    assert _normalize_model("l") == "flash-lite"
    assert _normalize_model("lite") == "flash-lite"
    assert _normalize_model("pro") == "pro"


def test_normalize_model_passthrough_unknown_alias():
    assert _normalize_model("gemini-3-pro") == "gemini-3-pro"


def test_normalize_model_is_case_insensitive():
    """MODEL_ALIASES 的 key 是小写单字母/词；model.lower() 后查表。"""
    assert _normalize_model("F") == "flash"
    assert _normalize_model("f") == "flash"
    assert _normalize_model("P") == "pro"
    assert _normalize_model("L") == "flash-lite"
    # "flash" 不是 alias key（是 canonical name），passthrough 但保持原大小写
    assert _normalize_model("FLASH") == "FLASH"


def test_normalize_media_type_resolves_aliases():
    assert _normalize_media_type("img") == "image"
    assert _normalize_media_type("picture") == "image"
    assert _normalize_media_type("photo") == "image"


def test_normalize_media_type_passthrough_unknown():
    assert _normalize_media_type("video") == "video"
    assert _normalize_media_type("music") == "music"


def test_normalize_media_type_is_case_insensitive():
    assert _normalize_media_type("IMG") == "image"


# ---------------------------------------------------------------------------
# B. _format_response（images / videos / audio_url / backend_label / remote_chat_id）
# ---------------------------------------------------------------------------


def _ns(**kwargs):
    """构造带属性访问的 SimpleNamespace response。"""
    return SimpleNamespace(**kwargs)


def test_format_response_text_only():
    resp = _ns(text="hello")
    result = _format_response(resp)
    assert result[0].text == "hello"


def test_format_response_empty_text_yields_empty_string():
    resp = _ns(text="")
    result = _format_response(resp)
    assert result[0].text == ""


def test_format_response_appends_image_urls():
    resp = _ns(
        text="txt",
        images=[_ns(url="http://img1"), _ns(url="http://img2"), _ns(url="")],
    )
    result = _format_response(resp)
    assert "[Image 1]: http://img1" in result[0].text
    assert "[Image 2]: http://img2" in result[0].text
    # 空 url 不输出
    assert "[Image 3]" not in result[0].text


def test_format_response_appends_video_urls():
    resp = _ns(text="txt", videos=[_ns(url="http://vid1")])
    result = _format_response(resp)
    assert "[Video 1]: http://vid1" in result[0].text


def test_format_response_appends_audio_url():
    resp = _ns(text="txt", audio_url="http://audio")
    result = _format_response(resp)
    assert "[Audio]: http://audio" in result[0].text


def test_format_response_inserts_backend_label_prefix():
    resp = _ns(text="txt")
    result = _format_response(resp, backend_label="Lyria 3 Pro")
    assert result[0].text.startswith("Backend: Lyria 3 Pro\n")
    assert "txt" in result[0].text


def test_format_response_inserts_backend_label_with_note():
    resp = _ns(text="txt")
    result = _format_response(resp, backend_label="Lyria 3", backend_note="Pro redo")
    text = result[0].text
    assert "Backend: Lyria 3" in text
    assert "Pro redo" in text


def test_format_response_appends_remote_chat_id(monkeypatch):
    monkeypatch.setattr(skill_server, "extract_remote_chat_id", lambda _r: "c_abc")
    resp = _ns(text="txt")
    result = _format_response(resp)
    assert "Remote chat ID: c_abc" in result[0].text


def test_format_response_skips_remote_chat_id_when_none(monkeypatch):
    monkeypatch.setattr(skill_server, "extract_remote_chat_id", lambda _r: None)
    resp = _ns(text="txt")
    result = _format_response(resp)
    assert "Remote chat ID" not in result[0].text


def test_format_response_combined_media_and_backend():
    resp = _ns(
        text="txt",
        images=[_ns(url="http://img")],
        videos=[_ns(url="http://vid")],
        audio_url="http://audio",
    )
    result = _format_response(resp, media_type="image", backend_label="Nano Banana 2")
    text = result[0].text
    assert "Backend: Nano Banana 2" in text
    assert "[Image 1]: http://img" in text
    assert "[Video 1]: http://vid" in text
    assert "[Audio]: http://audio" in text


# ---------------------------------------------------------------------------
# C. PromptManager（__init__ / _load / _save / list_all / get_by_name / create / delete）
# ---------------------------------------------------------------------------


def test_prompt_manager_load_returns_empty_when_file_missing(tmp_path):
    mgr = PromptManager(tmp_path / "missing.json")
    assert mgr.list_all() == []


def test_prompt_manager_load_reads_existing_file(tmp_path):
    f = tmp_path / "prompts.json"
    f.write_text(json.dumps({
        "version": "1.0",
        "prompts": {"p1": {"id": "p1", "name": "Prompt 1", "content": "c1", "category": "g"}},
    }), encoding="utf-8")
    mgr = PromptManager(f)
    items = mgr.list_all()
    assert len(items) == 1
    assert items[0]["name"] == "Prompt 1"


def test_prompt_manager_load_handles_invalid_json(tmp_path):
    f = tmp_path / "prompts.json"
    f.write_text("not valid json {{{", encoding="utf-8")
    mgr = PromptManager(f)
    # 损坏 JSON 触发 except 分支，_data 重置为 {}
    assert mgr.list_all() == []


def test_prompt_manager_load_handles_missing_prompts_key(tmp_path):
    f = tmp_path / "prompts.json"
    f.write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    mgr = PromptManager(f)
    assert mgr.list_all() == []


def test_prompt_manager_save_persists_to_file(tmp_path, monkeypatch):
    # _save 调 _ensure_config_dir 使用模块级 CONFIG_DIR，monkeypatch 到 tmp_path
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    f = tmp_path / "prompts.json"
    mgr = PromptManager(f)
    mgr.create("Test", "content")
    assert f.exists()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert "prompts" in data
    assert "test" in data["prompts"]


def test_prompt_manager_save_handles_io_error(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    f = tmp_path / "prompts.json"
    mgr = PromptManager(f)

    # 让 open 抛 IOError 触发 except 分支
    real_open = open

    def raising_open(path, *args, **kwargs):
        if str(path) == str(f) and "w" in (args[0] if args else kwargs.get("mode", "")):
            raise IOError("disk full")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", raising_open)
    # 不抛异常，仅记录日志
    mgr._save()


def test_prompt_manager_list_all_sorts_by_name_case_insensitive(tmp_path):
    f = tmp_path / "prompts.json"
    f.write_text(json.dumps({"prompts": {
        "z": {"id": "z", "name": "Zebra", "content": "", "category": "g"},
        "a": {"id": "a", "name": "apple", "content": "", "category": "g"},
        "m": {"id": "m", "name": "Mango", "content": "", "category": "g"},
    }}), encoding="utf-8")
    mgr = PromptManager(f)
    names = [p["name"] for p in mgr.list_all()]
    assert names == ["apple", "Mango", "Zebra"]


def test_prompt_manager_get_by_name_is_case_insensitive(tmp_path):
    f = tmp_path / "prompts.json"
    f.write_text(json.dumps({"prompts": {
        "p1": {"id": "p1", "name": "Test Prompt", "content": "c"},
    }}), encoding="utf-8")
    mgr = PromptManager(f)
    assert mgr.get_by_name("TEST PROMPT") is not None
    assert mgr.get_by_name("test prompt") is not None
    assert mgr.get_by_name("missing") is None


def test_prompt_manager_create_generates_id_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    f = tmp_path / "prompts.json"
    mgr = PromptManager(f)
    pid = mgr.create("My Cool Prompt", "content", category="custom")
    assert pid == "my_cool_prompt"
    prompt = mgr.get_by_name("My Cool Prompt")
    assert prompt is not None
    assert prompt["content"] == "content"
    assert prompt["category"] == "custom"


def test_prompt_manager_delete_removes_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    f = tmp_path / "prompts.json"
    mgr = PromptManager(f)
    mgr.create("ToDelete", "c")
    assert mgr.delete("ToDelete") is True
    assert mgr.get_by_name("ToDelete") is None


def test_prompt_manager_delete_returns_false_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    f = tmp_path / "prompts.json"
    mgr = PromptManager(f)
    assert mgr.delete("nonexistent") is False


# ---------------------------------------------------------------------------
# D. 配置 helper（_ensure_config_dir / _init_default_prompts / get_prompts 单例）
# ---------------------------------------------------------------------------


def test_ensure_config_dir_creates_directory(tmp_path, monkeypatch):
    target = tmp_path / "newconfig"
    monkeypatch.setattr(skill_server, "CONFIG_DIR", target)
    _ensure_config_dir()
    assert target.exists()


def test_ensure_config_dir_is_idempotent(tmp_path, monkeypatch):
    target = tmp_path / "newconfig"
    target.mkdir()
    monkeypatch.setattr(skill_server, "CONFIG_DIR", target)
    # 不抛异常
    _ensure_config_dir()
    assert target.exists()


def test_init_default_prompts_copies_when_missing(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    prompts_file = config_dir / "prompts.json"
    default_file = tmp_path / "prompts_default.json"
    default_file.write_text(json.dumps({"prompts": {"d": {"id": "d", "name": "Default", "content": "c"}}}),
                            encoding="utf-8")
    monkeypatch.setattr(skill_server, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(skill_server, "PROMPTS_FILE", prompts_file)
    monkeypatch.setattr(skill_server, "DEFAULT_PROMPTS_FILE", default_file)
    _init_default_prompts()
    assert prompts_file.exists()
    data = json.loads(prompts_file.read_text(encoding="utf-8"))
    assert "d" in data["prompts"]


def test_init_default_prompts_skips_when_already_exists(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    prompts_file = config_dir / "prompts.json"
    prompts_file.write_text('{"prompts": {}}', encoding="utf-8")
    default_file = tmp_path / "prompts_default.json"
    default_file.write_text('{"prompts": {"d": {"name": "Default"}}}', encoding="utf-8")
    monkeypatch.setattr(skill_server, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(skill_server, "PROMPTS_FILE", prompts_file)
    monkeypatch.setattr(skill_server, "DEFAULT_PROMPTS_FILE", default_file)
    _init_default_prompts()
    # 不覆盖已存在的文件
    data = json.loads(prompts_file.read_text(encoding="utf-8"))
    assert data == {"prompts": {}}


def test_init_default_prompts_skips_when_no_default_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    prompts_file = config_dir / "prompts.json"
    default_file = tmp_path / "nonexistent_default.json"
    monkeypatch.setattr(skill_server, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(skill_server, "PROMPTS_FILE", prompts_file)
    monkeypatch.setattr(skill_server, "DEFAULT_PROMPTS_FILE", default_file)
    _init_default_prompts()
    assert not prompts_file.exists()


def test_get_prompts_singleton_caches_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(skill_server, "_prompt_manager", None)
    monkeypatch.setattr(skill_server, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(skill_server, "PROMPTS_FILE", tmp_path / "prompts.json")
    first = get_prompts()
    second = get_prompts()
    assert first is second


# ---------------------------------------------------------------------------
# E. chat 工具（invalid image / session 命中 / learning_mode 注入 / 顶层 except）
# ---------------------------------------------------------------------------


def _patch_client_seams(monkeypatch, client=None):
    """统一 patch client_wrapper 接缝。"""
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: client)
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock(return_value=None))
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", AsyncMock(return_value=None))


def test_chat_returns_error_when_image_path_invalid(monkeypatch):
    """validate_optional_image_path 失败时早退返回 Error: {image_error}。"""
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (False, None, "file not found"))
    result = _run(skill_server.chat(message="hi"))
    assert result[0].text == "Error: file not found"


def test_chat_generates_content_without_session(monkeypatch):
    """无 session_id 走 client.generate_content 路径。"""
    client = SimpleNamespace(generate_content=AsyncMock(return_value=_ns(text="response text")))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    result = _run(skill_server.chat(message="hi", model="flash", learning_mode="quiz"))
    assert "response text" in result[0].text
    # learning_mode 注入
    kwargs = client.generate_content.call_args.kwargs
    assert kwargs["learning_mode"] == "quiz"
    assert kwargs["model"] == "gemini-3-flash"  # flash → resolve_model_name


def test_chat_omits_learning_mode_when_none(monkeypatch):
    """learning_mode=None 时 kwargs 不含 learning_mode 键。"""
    client = SimpleNamespace(generate_content=AsyncMock(return_value=_ns(text="t")))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    _run(skill_server.chat(message="hi", learning_mode=None))
    kwargs = client.generate_content.call_args.kwargs
    assert "learning_mode" not in kwargs


def test_chat_session_path_uses_session_send_message(monkeypatch):
    """session_id 命中 _sessions 时走 session.send_message，learning_mode 从 session 回退。"""
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="session response")),
        cid="c_sess",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "sess_1": {
            "session": fake_session,
            "model": "flash",
            "thinking_level": "standard",
            "learning_mode": "default_quiz",
        },
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    result = _run(skill_server.chat(message="hi", session_id="sess_1"))
    assert "session response" in result[0].text
    kwargs = fake_session.send_message.call_args.kwargs
    # learning_mode 从 session_entry 回退
    assert kwargs["learning_mode"] == "default_quiz"


def test_chat_session_path_explicit_learning_mode_overrides_session(monkeypatch):
    """显式 learning_mode 优先于 session_entry 的 learning_mode。"""
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="t")),
        cid="c_sess",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "sess_1": {"session": fake_session, "model": "flash",
                   "thinking_level": "standard", "learning_mode": "default"},
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    _run(skill_server.chat(message="hi", session_id="sess_1", learning_mode="explicit"))
    kwargs = fake_session.send_message.call_args.kwargs
    assert kwargs["learning_mode"] == "explicit"


def test_chat_session_path_no_learning_mode_anywhere_omits_kwarg(monkeypatch):
    """session_entry 与显式均无 learning_mode 时不注入。"""
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="t")),
        cid="c_sess",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "sess_1": {"session": fake_session, "model": "flash",
                   "thinking_level": "standard", "learning_mode": None},
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    _run(skill_server.chat(message="hi", session_id="sess_1", learning_mode=None))
    kwargs = fake_session.send_message.call_args.kwargs
    assert "learning_mode" not in kwargs


def test_chat_top_level_exception_returns_error_text(monkeypatch):
    """client.generate_content 抛异常 → _error_text(e, 'Chat')。"""
    client = SimpleNamespace(generate_content=AsyncMock(side_effect=RuntimeError("network down")))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    result = _run(skill_server.chat(message="hi"))
    assert result[0].text == "Error: network down"


# ---------------------------------------------------------------------------
# F. history 工具（list / search / read / export / delete + Invalid action + except）
# ---------------------------------------------------------------------------


def test_history_list_returns_no_chats_when_empty(monkeypatch):
    client = SimpleNamespace()
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="list"))
    assert result[0].text == "No chats"


def test_history_list_renders_chats(monkeypatch):
    chats = [SimpleNamespace(title="Chat A", cid="c_1"), SimpleNamespace(title="Chat B", cid="c_2")]
    client = SimpleNamespace(list_chats=lambda: chats)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="list"))
    assert "1. Chat A (c_1)" in result[0].text
    assert "2. Chat B (c_2)" in result[0].text


def test_history_list_appends_next_offset_when_has_more(monkeypatch):
    chats = [SimpleNamespace(title=f"Chat {i}", cid=f"c_{i}") for i in range(10)]
    client = SimpleNamespace(list_chats=lambda: chats)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="list", limit=3, offset=0))
    assert "next_offset=3" in result[0].text


def test_history_search_requires_query(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="search", query=""))
    assert result[0].text == "query required"


def test_history_search_scan_turns_requires_read_chat(monkeypatch):
    client = SimpleNamespace(list_chats=lambda: [])
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="search", query="x", scan_turns=True))
    assert result[0].text == "read_chat unavailable"


def test_history_search_no_matches(monkeypatch):
    chats = [SimpleNamespace(title="Alpha", cid="c_1")]
    client = SimpleNamespace(list_chats=lambda: chats)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="search", query="zzz"))
    assert result[0].text == "No matches"


def test_history_search_matches_by_title(monkeypatch):
    chats = [SimpleNamespace(title="Alpha Report", cid="c_1"),
             SimpleNamespace(title="Beta", cid="c_2")]
    client = SimpleNamespace(list_chats=lambda: chats)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="search", query="alpha"))
    assert "Alpha Report (c_1)" in result[0].text
    assert "Beta" not in result[0].text


def test_history_search_matches_by_id(monkeypatch):
    chats = [SimpleNamespace(title="X", cid="c_special")]
    client = SimpleNamespace(list_chats=lambda: chats)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="search", query="special"))
    assert "c_special" in result[0].text


def test_history_search_scan_turns_collects_snippets(monkeypatch):
    """scan_turns=True 时读 turn 并匹配 query，命中收集 snippet。"""
    chat_item = SimpleNamespace(title="X", cid="c_1")
    client = SimpleNamespace(list_chats=lambda: [chat_item], read_chat=AsyncMock())

    async def fake_read_turns(_client, _cid, _limit, _max_chars):
        return None, [{"role": "user", "text": "findme here"}]

    monkeypatch.setattr(skill_server, "_read_chat_turns", fake_read_turns)
    monkeypatch.setattr(skill_server, "_chat_to_dict",
                        lambda c: {"id": getattr(c, "cid", ""), "title": getattr(c, "title", "")})
    monkeypatch.setattr(skill_server, "_turn_matches_query",
                        lambda turn, q: "findme" in turn["text"])
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="search", query="findme", scan_turns=True))
    assert "turn 1 user" in result[0].text
    assert "findme" in result[0].text


def test_history_search_appends_next_offset(monkeypatch):
    chats = [SimpleNamespace(title=f"c{i}", cid=f"i{i}") for i in range(10)]
    client = SimpleNamespace(list_chats=lambda: chats)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="search", query="c", limit=3))
    assert "next_offset=3" in result[0].text


def test_history_read_requires_chat_id(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="read"))
    assert result[0].text == "chat_id required"


def test_history_read_requires_read_chat_capability(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="read", chat_id="c_1"))
    assert result[0].text == "read_chat unavailable"


def test_history_read_returns_no_turns_when_empty(monkeypatch):
    client = SimpleNamespace(read_chat=AsyncMock(return_value=_ns(turns=[])))
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="read", chat_id="c_1"))
    assert result[0].text == "No turns"


def test_history_read_renders_turns(monkeypatch):
    chat = _ns(turns=[_ns(role="user", text="hello"), _ns(role="model", text="world")])
    client = SimpleNamespace(read_chat=AsyncMock(return_value=chat))
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="read", chat_id="c_1"))
    assert "user: hello" in result[0].text
    assert "model: world" in result[0].text


def test_history_read_handles_chat_none(monkeypatch):
    client = SimpleNamespace(read_chat=AsyncMock(return_value=None))
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="read", chat_id="c_1"))
    assert result[0].text == "No turns"


def test_history_export_requires_chat_id(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="export"))
    assert result[0].text == "chat_id required"


def test_history_export_requires_read_chat(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="export", chat_id="c_1"))
    assert result[0].text == "read_chat unavailable"


def test_history_export_renders_markdown(monkeypatch):
    client = SimpleNamespace(read_chat=AsyncMock())

    async def fake_read_turns(_client, _cid, _limit, _max_chars):
        return None, [{"role": "user", "text": "t1"}]

    monkeypatch.setattr(skill_server, "_read_chat_turns", fake_read_turns)
    monkeypatch.setattr(skill_server, "_chat_export_payload",
                        lambda cid, h, t, m, _limit, mc: {"id": cid, "turns": len(t)})
    monkeypatch.setattr(skill_server, "_format_chat_export_markdown",
                        lambda p: f"EXPORT {p['id']} turns={p['turns']}")
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="export", chat_id="c_1"))
    assert result[0].text == "EXPORT c_1 turns=1"


def test_history_delete_requires_chat_id(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="delete"))
    assert result[0].text == "chat_id required"


def test_history_delete_requires_delete_chat_capability(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="delete", chat_id="c_1"))
    assert result[0].text == "delete_chat unavailable"


def test_history_delete_calls_delete_chat(monkeypatch):
    client = SimpleNamespace(delete_chat=AsyncMock())
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="delete", chat_id="c_1"))
    assert result[0].text == "Deleted: c_1"
    client.delete_chat.assert_awaited_once_with("c_1")


def test_history_invalid_action_returns_message(monkeypatch):
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.history(action="unknown"))
    assert result[0].text == "Invalid action"


def test_history_top_level_exception_returns_error_text(monkeypatch):
    """get_gemini_client 抛异常 → _error_text(e, 'History')。"""
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock())
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", AsyncMock())
    result = _run(skill_server.history(action="list"))
    assert result[0].text == "Error: boom"


# ---------------------------------------------------------------------------
# G. account 子处理器（models / features / links / library / notebooks / modes / status）
# ---------------------------------------------------------------------------


def test_account_models_returns_no_models_when_empty(monkeypatch):
    client = SimpleNamespace()
    result = _run(_account_models(client))
    assert result[0].text == "No models"


def test_account_models_renders_model_list(monkeypatch):
    models = [
        SimpleNamespace(display_name="Flash", model_name="gemini-3-flash", is_available=True),
        SimpleNamespace(display_name="Pro", model_name="gemini-3-pro", is_available=False),
    ]
    client = SimpleNamespace(list_models=lambda: models)
    result = _run(_account_models(client))
    assert "Flash: gemini-3-flash (available)" in result[0].text
    assert "Pro: gemini-3-pro (unavailable)" in result[0].text


def test_account_features_returns_unavailable_when_no_batch_execute(monkeypatch):
    client = SimpleNamespace()
    result = _run(_account_features(client))
    assert result[0].text == "feature probes unavailable"


def test_account_features_probes_concurrently(monkeypatch):
    """WEB_FEATURE_PROBES 各 probe 并发执行，正常返回 ok 状态。"""
    fake_response = SimpleNamespace(text="resp", status_code=200)
    client = SimpleNamespace(
        _batch_execute=AsyncMock(return_value=fake_response),
    )
    monkeypatch.setattr(skill_server, "_summarize_probe_response", lambda _t, _r: {"reject_code": None})
    result = _run(_account_features(client))
    assert "ok" in result[0].text


def test_account_features_probe_exception_returns_type_name(monkeypatch):
    """单个 probe 抛异常时返回 type(e).__name__。"""
    client = SimpleNamespace(
        _batch_execute=AsyncMock(side_effect=ConnectionError("net err")),
    )
    result = _run(_account_features(client))
    assert "ConnectionError" in result[0].text


def test_account_features_probe_reject_code(monkeypatch):
    """summary 有 reject_code 时返回 reject=... 状态。"""
    fake_response = SimpleNamespace(text="resp", status_code=200)
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_summarize_probe_response",
                        lambda _t, _r: {"reject_code": 42})
    result = _run(_account_features(client))
    assert "reject=42" in result[0].text


def test_account_links_returns_no_links_when_empty(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[]])
    result = _run(_account_links(client))
    assert result[0].text == "No public links"


def test_account_links_renders_links(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies",
                        lambda _t, _r: [[{"title": "Link 1", "id": "l1", "url": "http://l1"}]])
    monkeypatch.setattr(skill_server, "_parse_public_link_entry", lambda item: item)
    result = _run(_account_links(client))
    assert "Link 1 (l1) http://l1" in result[0].text


def test_account_library_returns_no_capabilities_when_empty(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[]])
    result = _run(_account_library(client))
    assert result[0].text == "No library capabilities"


def test_account_library_renders_capabilities(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies",
                        lambda _t, _r: [[[{"name": "Cap1", "description": "desc"}]]])
    monkeypatch.setattr(skill_server, "_parse_library_capability", lambda item: item)
    result = _run(_account_library(client))
    assert "Cap1: desc" in result[0].text


def test_account_notebooks_returns_empty_message(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_fetch_native_notebooks", AsyncMock(return_value=([], {})))
    result = _run(_account_notebooks(client))
    assert result[0].text == "No native Gemini notebooks"


def test_account_notebooks_renders_list(monkeypatch):
    client = SimpleNamespace()
    notebooks = [{"title": "Notebook 1", "id": "n1", "source_count": 3}]
    monkeypatch.setattr(skill_server, "_fetch_native_notebooks",
                        AsyncMock(return_value=(notebooks, {})))
    result = _run(_account_notebooks(client))
    assert "Notebook 1 (n1) sources=3" in result[0].text


def test_account_modes_returns_empty_message(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[]])
    result = _run(_account_modes(client))
    assert result[0].text == "No mode status entries"


def test_account_modes_renders_entries(monkeypatch):
    """body 需为 len>1 的 list 且 body[1] 是 list → entries 来自 body[1]。"""
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    # bodies[0] = [[], [{...}]] → len 2, body[1] = [{...}]
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies",
                        lambda _t, _r: [[[], [{"mode_id": "m1", "available": True, "quota_value": 5, "state": "ok"}]]])
    monkeypatch.setattr(skill_server, "_parse_tool_mode_entry", lambda item: item)
    result = _run(_account_modes(client))
    assert "mode_id=m1 available=True quota=5 state=ok" in result[0].text


def test_account_status_returns_unavailable_without_inspect(monkeypatch):
    client = SimpleNamespace()
    result = _run(_account_status(client))
    assert result[0].text == "account inspection unavailable"


def test_account_status_returns_loaded_when_summary_empty(monkeypatch):
    client = SimpleNamespace(inspect_account_status=AsyncMock(return_value={"summary": {}}))
    result = _run(_account_status(client))
    assert result[0].text == "Account status loaded"


def test_account_status_renders_summary(monkeypatch):
    client = SimpleNamespace(inspect_account_status=AsyncMock(
        return_value={"summary": {"tier": "plus", "quota": "100"}}))
    result = _run(_account_status(client))
    assert "tier: plus" in result[0].text
    assert "quota: 100" in result[0].text


def test_account_status_handles_non_dict_status(monkeypatch):
    """status 非 dict 时 isinstance 检查跳过，summary 为空。"""
    client = SimpleNamespace(inspect_account_status=AsyncMock(return_value="not a dict"))
    result = _run(_account_status(client))
    assert result[0].text == "Account status loaded"


def test_account_top_level_exception_returns_error_text(monkeypatch):
    """auth_free handler 抛异常 → _error_text(e, 'Account')。

    _ACCOUNT_AUTH_FREE_ACTIONS 在模块加载时绑定原函数引用，
    patch 模块属性无法改变 dict 内引用；改为 patch handler 调用的底层函数。
    """
    monkeypatch.setattr(skill_server, "_web_capabilities_payload",
                        lambda: (_ for _ in ()).throw(RuntimeError("cap err")))
    result = _run(skill_server.account(action="capabilities"))
    assert result[0].text == "Error: cap err"


# ---------------------------------------------------------------------------
# H. scheduled 子处理器（list / get / create / delete + verification 树）
# ---------------------------------------------------------------------------


def test_scheduled_list_empty_with_hint(monkeypatch):
    """entries 空但 diagnostic 有 empty_hint → 追加 Diagnostic 行。"""
    client = SimpleNamespace(_batch_execute=AsyncMock())
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {"empty_hint": "no actions found"})))
    result = _run(_scheduled_list(client))
    assert "No scheduled actions" in result[0].text
    assert "Diagnostic: no actions found" in result[0].text


def test_scheduled_list_renders_entries(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    entries = [{"title": "Daily Task", "id": "s1", "schedule_label": "9:00"}]
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=(entries, {})))
    result = _run(_scheduled_list(client))
    assert "Daily Task (s1) 9:00" in result[0].text


def test_scheduled_get_requires_action_id(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = _run(_scheduled_get(client, "  "))
    assert result[0].text == "action_id required"


def test_scheduled_get_not_found_with_matched_task_false(monkeypatch):
    """item 为空且 diagnostic.matched_task is False → status=not_readable_by_id。"""
    client = SimpleNamespace(_batch_execute=AsyncMock())
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {"matched_task": False})))
    result = _run(_scheduled_get(client, "s1"))
    assert "not_readable_by_id" in result[0].text


def test_scheduled_get_not_found_default_status(monkeypatch):
    """item 为空且 diagnostic 无 matched_task → status=not_found_or_wrong_account。"""
    client = SimpleNamespace(_batch_execute=AsyncMock())
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    result = _run(_scheduled_get(client, "s1"))
    assert "not_found_or_wrong_account" in result[0].text


def test_scheduled_get_renders_found_item(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    item = {"id": "s1", "title": "My Task", "enabled": True, "task_state": "running",
            "schedule_label": "daily"}
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(item, {})))
    result = _run(_scheduled_get(client, "s1"))
    text = result[0].text
    assert "My Task" in text
    assert "[enabled" in text
    assert "state=running" in text
    assert "daily" in text


def test_scheduled_get_disabled_state(monkeypatch):
    """enabled=False → disabled。无 task_state → 不追加 state。"""
    client = SimpleNamespace(_batch_execute=AsyncMock())
    item = {"id": "s1", "title": "T", "enabled": False}
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(item, {})))
    result = _run(_scheduled_get(client, "s1"))
    assert "[disabled]" in result[0].text
    assert "state=" not in result[0].text


def test_scheduled_create_requires_title(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = _run(_scheduled_create(client, "  ", "instr", 9, "Asia/Shanghai"))
    assert result[0].text == "title required"


def test_scheduled_create_requires_instructions(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = _run(_scheduled_create(client, "Title", "  ", 9, "Asia/Shanghai"))
    assert result[0].text == "instructions required"


def test_scheduled_create_rejects_invalid_hour(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = _run(_scheduled_create(client, "T", "I", 24, "Asia/Shanghai"))
    assert result[0].text == "hour must be 0..23"
    result = _run(_scheduled_create(client, "T", "I", -1, "Asia/Shanghai"))
    assert result[0].text == "hour must be 0..23"


def test_scheduled_create_visible_in_registry(monkeypatch):
    """created_id 命中 registry_entries → verification_status=visible_in_registry，无 suffix。"""
    fake_response = SimpleNamespace(text="t")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["created_id"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body",
                        lambda b: {"id": "new_1"})
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"id": "new_1"}], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload",
                        lambda *args: "payload")
    result = _run(_scheduled_create(client, "Title", "Instr", 9, "Asia/Shanghai"))
    # visible=True → 无 suffix
    assert result[0].text == "Created: new_1"


def test_scheduled_create_not_visible_in_nonempty_registry(monkeypatch):
    """created_id 不在 registry 但 registry 非空 → not_visible_in_nonempty_registry。"""
    fake_response = SimpleNamespace(text="t")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["x"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body",
                        lambda b: {"id": "new_1"})
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"id": "other"}], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload", lambda *a: "p")
    result = _run(_scheduled_create(client, "T", "I", 9, "Asia/Shanghai"))
    assert "not_visible_in_nonempty_registry" in result[0].text


def test_scheduled_create_registry_empty_unverified(monkeypatch):
    """created_id 存在但 registry 为空 → registry_empty_unverified。"""
    fake_response = SimpleNamespace(text="t")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["x"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body",
                        lambda b: {"id": "new_1"})
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload", lambda *a: "p")
    result = _run(_scheduled_create(client, "T", "I", 9, "Asia/Shanghai"))
    assert "registry_empty_unverified" in result[0].text


def test_scheduled_create_readable_by_id_registry_empty(monkeypatch):
    """registry 空但 task_by_id 可读 → readable_by_id_registry_empty。"""
    fake_response = SimpleNamespace(text="t")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["x"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body",
                        lambda b: {"id": "new_1"})
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=({"id": "new_1"}, {})))
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload", lambda *a: "p")
    result = _run(_scheduled_create(client, "T", "I", 9, "Asia/Shanghai"))
    assert "readable_by_id_registry_empty" in result[0].text


def test_scheduled_create_readable_by_id_not_visible(monkeypatch):
    """registry 非空且不 visible 但 task_by_id 可读 → readable_by_id_not_visible_in_registry。"""
    fake_response = SimpleNamespace(text="t")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["x"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body",
                        lambda b: {"id": "new_1"})
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"id": "other"}], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=({"id": "new_1"}, {})))
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload", lambda *a: "p")
    result = _run(_scheduled_create(client, "T", "I", 9, "Asia/Shanghai"))
    assert "readable_by_id_not_visible_in_registry" in result[0].text


def test_scheduled_create_no_created_id(monkeypatch):
    """created_id 为空 → 不进入 verification 分支，suffix 用 title。"""
    fake_response = SimpleNamespace(text="t")
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=fake_response))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["x"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body",
                        lambda b: {"id": ""})
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload", lambda *a: "p")
    result = _run(_scheduled_create(client, "My Title", "I", 9, "Asia/Shanghai"))
    assert "Created: My Title" in result[0].text
    assert "not_attempted" in result[0].text


def test_scheduled_delete_requires_action_id(monkeypatch):
    client = SimpleNamespace(_batch_execute=AsyncMock())
    result = _run(_scheduled_delete(client, "  "))
    assert result[0].text == "action_id required"


def test_scheduled_delete_rpc_unconfirmed_when_no_bodies(monkeypatch):
    """_extract_rpc_bodies 返回空 → verification_status=rpc_unconfirmed。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [])
    result = _run(_scheduled_delete(client, "s1"))
    assert "rpc_unconfirmed" in result[0].text


def test_scheduled_delete_still_visible_in_registry(monkeypatch):
    """删除后仍 visible → still_visible_in_registry。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [["x"]])
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"id": "s1"}], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    result = _run(_scheduled_delete(client, "s1"))
    assert "still_visible_in_registry" in result[0].text


def test_scheduled_delete_deleted_state_by_id(monkeypatch):
    """task_after_delete.task_state_id == 6 → deleted_state_by_id。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [["x"]])
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=({"task_state_id": 6}, {})))
    result = _run(_scheduled_delete(client, "s1"))
    assert "deleted_state_by_id" in result[0].text


def test_scheduled_delete_registry_empty_active_or_unknown_by_id(monkeypatch):
    """registry 空 + readable 但 task_state_id != 6 → registry_empty_active_or_unknown_by_id。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [["x"]])
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=({"task_state_id": 1}, {})))
    result = _run(_scheduled_delete(client, "s1"))
    assert "registry_empty_active_or_unknown_by_id" in result[0].text


def test_scheduled_delete_not_visible_active_or_unknown_by_id(monkeypatch):
    """registry 非空 + 不 visible + readable + state!=6 → not_visible_active_or_unknown_by_id。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [["x"]])
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"id": "other"}], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=({"task_state_id": 1}, {})))
    result = _run(_scheduled_delete(client, "s1"))
    assert "not_visible_active_or_unknown_by_id" in result[0].text


def test_scheduled_delete_registry_empty_not_readable_by_id(monkeypatch):
    """registry 空 + 不可读 → registry_empty_not_readable_by_id。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [["x"]])
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    result = _run(_scheduled_delete(client, "s1"))
    assert "registry_empty_not_readable_by_id" in result[0].text


def test_scheduled_delete_not_visible_not_readable_by_id(monkeypatch):
    """registry 非空 + 不 visible + 不可读 → not_visible_not_readable_by_id。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [["x"]])
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"id": "other"}], {})))
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=(None, {})))
    result = _run(_scheduled_delete(client, "s1"))
    assert "not_visible_not_readable_by_id" in result[0].text


def test_scheduled_main_returns_unavailable_when_no_batch_execute(monkeypatch):
    """client 无 _batch_execute → 'scheduled actions unavailable'。"""
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.scheduled(action="list"))
    assert result[0].text == "scheduled actions unavailable"


def test_scheduled_main_invalid_action(monkeypatch):
    """未知 action → 'Invalid action'。

    @mcp.tool 装饰器返回原函数；直接调用绕过 MCP 层 Literal 校验。
    """
    client = SimpleNamespace(_batch_execute=AsyncMock())
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.scheduled(action="unknown"))
    assert result[0].text == "Invalid action"


def test_scheduled_main_top_level_exception(monkeypatch):
    """get_gemini_client 抛异常 → _error_text(e, 'Scheduled action')。"""
    monkeypatch.setattr(skill_server, "get_gemini_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock())
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", AsyncMock())
    result = _run(skill_server.scheduled(action="list"))
    assert result[0].text == "Error: boom"


# ---------------------------------------------------------------------------
# I. create / edit 工具（invalid image + 顶层 except）
# ---------------------------------------------------------------------------


def test_create_returns_error_when_image_invalid(monkeypatch):
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (False, None, "bad image"))
    result = _run(skill_server.create(prompt="x"))
    assert result[0].text == "Error: bad image"


def test_create_top_level_exception(monkeypatch):
    """resolve_media_request 抛异常 → _error_text(e, 'Create')。"""
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "resolve_media_request",
                        lambda *a: (_ for _ in ()).throw(ValueError("bad media")))
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.create(prompt="x"))
    assert result[0].text == "Error: bad media"


def test_edit_returns_error_when_image_invalid(monkeypatch):
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (False, None, "bad image"))
    result = _run(skill_server.edit(image_path="x", prompt="y"))
    assert result[0].text == "Error: bad image"


def test_edit_top_level_exception(monkeypatch):
    """resolve_model_name 抛异常 → _error_text(e, 'Edit')。"""
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, "safe", None))
    monkeypatch.setattr(skill_server, "resolve_model_name",
                        lambda *a: (_ for _ in ()).throw(ValueError("bad model")))
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(skill_server.edit(image_path="x", prompt="y"))
    assert result[0].text == "Error: bad model"


# ---------------------------------------------------------------------------
# J. session 工具（_session_send learning_mode 注入 + 顶层 except）
# ---------------------------------------------------------------------------


def test_session_send_invalid_session_returns_error(monkeypatch):
    """session_id 不命中 _sessions → 'Invalid session: {id}'。"""
    monkeypatch.setattr(skill_server, "_sessions", {})
    _patch_client_seams(monkeypatch, SimpleNamespace())
    result = _run(_session_send("sess_missing", "msg", "standard", None, None, "flash"))
    assert result[0].text == "Invalid session: sess_missing"


def test_session_send_injects_learning_mode_from_session(monkeypatch):
    """learning_mode=None 时从 session_entry 回退。"""
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="t")),
        cid="c_s",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "s1": {"session": fake_session, "model": "flash",
               "thinking_level": "standard", "learning_mode": "default_quiz"},
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    _run(_session_send("s1", "msg", "standard", None, None, "flash"))
    kwargs = fake_session.send_message.call_args.kwargs
    assert kwargs["learning_mode"] == "default_quiz"


def test_session_send_explicit_learning_mode_overrides(monkeypatch):
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="t")),
        cid="c_s",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "s1": {"session": fake_session, "model": "flash",
               "thinking_level": "standard", "learning_mode": "default"},
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    _run(_session_send("s1", "msg", "standard", "explicit", None, "flash"))
    kwargs = fake_session.send_message.call_args.kwargs
    assert kwargs["learning_mode"] == "explicit"


def test_session_send_uses_session_thinking_level_when_default(monkeypatch):
    """thinking_level 参数为 'standard' 但 session_entry 有 'extended' 时用 session 的。"""
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="t")),
        cid="c_s",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "s1": {"session": fake_session, "model": "flash",
               "thinking_level": "extended", "learning_mode": None},
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    _run(_session_send("s1", "msg", "standard", None, None, "flash"))
    kwargs = fake_session.send_message.call_args.kwargs
    assert kwargs["thinking_level"] == "extended"


def test_session_top_level_exception(monkeypatch):
    """validate_optional_image_path 抛异常 → _error_text(e, 'Session')。"""
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (_ for _ in ()).throw(RuntimeError("img err")))
    result = _run(skill_server.session(action="list"))
    assert result[0].text == "Error: img err"


# ---------------------------------------------------------------------------
# K. doctor / cleanup / prompts / cookie 顶层 except
# ---------------------------------------------------------------------------


def test_doctor_top_level_exception(monkeypatch):
    """_doctor_payload 抛异常 → _error_text(e, 'Doctor')。"""
    monkeypatch.setattr(skill_server, "_doctor_payload",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("doc err")))
    result = _run(skill_server.doctor())
    assert result[0].text == "Error: doc err"


def test_cleanup_top_level_exception(monkeypatch):
    """_cleanup_test_artifacts_payload 抛异常 → _error_text(e, 'Cleanup')。"""
    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: SimpleNamespace())
    monkeypatch.setattr(skill_server, "initialize_client", AsyncMock())
    monkeypatch.setattr(skill_server, "cleanup_due_remote_chats", AsyncMock())
    monkeypatch.setattr(skill_server, "_cleanup_test_artifacts_payload",
                        AsyncMock(side_effect=RuntimeError("cleanup err")))
    result = _run(skill_server.cleanup())
    assert result[0].text == "Error: cleanup err"


def test_prompts_top_level_exception(monkeypatch):
    """get_prompts 抛异常 → _error_text(e, 'Prompts')。"""
    monkeypatch.setattr(skill_server, "get_prompts",
                        lambda: (_ for _ in ()).throw(RuntimeError("mgr err")))
    result = _run(skill_server.prompts(action="list"))
    assert result[0].text == "Error: mgr err"


def test_cookie_top_level_exception(monkeypatch):
    """get_cookie_status 抛异常 → _error_text(e, 'Cookie')。"""
    monkeypatch.setattr(skill_server, "get_cookie_status",
                        lambda: (_ for _ in ()).throw(RuntimeError("cookie err")))
    result = _run(skill_server.cookie(action="status"))
    assert result[0].text == "Error: cookie err"


# ---------------------------------------------------------------------------
# L. happy path 补齐（account_usage / account_scheduled / account_manifest /
#    create / edit / session_create+list+reset+dispatch / doctor / cleanup / history export list_chats）
# ---------------------------------------------------------------------------


def test_account_usage_renders_entries(monkeypatch):
    """_account_usage 并发 probe usage_quota + usage_model_state。

    结构：bodies[0] = [[{...}]] → bodies[0][0]=first=[{...}] 是 list，
    entries 从 first 的每个 item 解析。
    """
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies",
                        lambda _t, _r: [[[{"key": "k1", "limit_value": 100, "remaining_value": 50}]]])
    monkeypatch.setattr(skill_server, "_parse_usage_entry", lambda item: item)
    result = _run(_account_usage(client))
    assert "usage_quota" in result[0].text
    assert "key=k1 limit=100 remaining=50" in result[0].text


def test_account_usage_no_entries(monkeypatch):
    """bodies 空或结构不匹配 → 'No usage entries'。"""
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [])
    result = _run(_account_usage(client))
    assert result[0].text == "No usage entries"


def test_account_scheduled_renders_entries(monkeypatch):
    """_account_scheduled 从 scheduled_actions_registry probe 解析条目。"""
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    # bodies[0] = [[{...}]] → raw_entries from body[0]
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies",
                        lambda _t, _r: [[[{"title": "Daily", "id": "s1"}]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_task_entry",
                        lambda item, max_chars: {"title": "Daily", "id": "s1", "schedule_label": "9:00"})
    result = _run(_account_scheduled(client))
    assert "Daily (s1) 9:00" in result[0].text


def test_account_scheduled_empty(monkeypatch):
    client = SimpleNamespace()
    monkeypatch.setattr(skill_server, "_get_probe", lambda s, n: {"rpcid": "x", "payload": "[]", "source_path": "/"})
    monkeypatch.setattr(skill_server, "_execute_observed_rpc", AsyncMock(return_value=_ns(text="t")))
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [])
    result = _run(_account_scheduled(client))
    assert result[0].text == "No scheduled actions"


def test_account_manifest_renders(monkeypatch):
    """_account_manifest 调 _tool_manifest_payload + _format_tool_manifest_markdown。"""
    monkeypatch.setattr(skill_server, "_tool_manifest_payload", lambda scope: {"tools": []})
    monkeypatch.setattr(skill_server, "_format_tool_manifest_markdown", lambda p: "MANIFEST MARKDOWN")
    result = _run(skill_server.account(action="manifest"))
    assert result[0].text == "MANIFEST MARKDOWN"


def test_account_client_action_dispatch(monkeypatch):
    """action='models' 走 _ACCOUNT_CLIENT_ACTIONS dispatch（client-based 路径）。"""
    models = [SimpleNamespace(display_name="Flash", model_name="gemini-3-flash", is_available=True)]
    client = SimpleNamespace(list_models=lambda: models)
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.account(action="models"))
    assert "Flash: gemini-3-flash (available)" in result[0].text


def test_account_unknown_action_falls_back_to_status(monkeypatch):
    """未知 action 走 _account_status 兜底（_ACCOUNT_CLIENT_ACTIONS.get(action, _account_status)）。"""
    client = SimpleNamespace(inspect_account_status=AsyncMock(return_value={"summary": {"tier": "free"}}))
    _patch_client_seams(monkeypatch, client)
    # 直接调用绕过 Literal 校验
    result = _run(skill_server.account(action="unknown"))
    assert "tier: free" in result[0].text


def test_create_image_happy_path(monkeypatch):
    """create 生成 image：resolve_media_request + generate_content + _format_response。"""
    response = _ns(text="image generated", images=[_ns(url="http://img")])
    client = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "validate_optional_image_path", lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "resolve_media_request",
                        lambda model, media_type, thinking_level: {
                            "request_model": "gemini-3-flash",
                            "backend_label": "Nano Banana 2",
                            "note": "first gen",
                        })
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    result = _run(skill_server.create(prompt="a cat", type="image"))
    text = result[0].text
    assert "Backend: Nano Banana 2" in text
    assert "image generated" in text
    assert "[Image 1]: http://img" in text
    kwargs = client.generate_content.call_args.kwargs
    assert kwargs["prompt"] == "Generate image: a cat"
    assert kwargs["model"] == "gemini-3-flash"


def test_create_music_happy_path(monkeypatch):
    """create 生成 music：prompt 前缀为 'Create music: '。"""
    response = _ns(text="music done", audio_url="http://audio")
    client = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "validate_optional_image_path", lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "resolve_media_request",
                        lambda model, media_type, thinking_level: {
                            "request_model": "gemini-3-pro",
                            "backend_label": "Lyria 3 Pro",
                            "note": "extended",
                        })
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    result = _run(skill_server.create(prompt="jazz", type="music", model="pro",
                                       thinking_level="extended"))
    kwargs = client.generate_content.call_args.kwargs
    assert kwargs["prompt"] == "Create music: jazz"
    assert "[Audio]: http://audio" in result[0].text


def test_edit_happy_path(monkeypatch):
    """edit 调 generate_content 带 files=[safe_image_path]。"""
    response = _ns(text="edited", images=[_ns(url="http://edited")])
    client = SimpleNamespace(generate_content=AsyncMock(return_value=response))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, "/safe/path.png", None))
    monkeypatch.setattr(skill_server, "resolve_model_name", lambda m: "gemini-3-flash")
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    result = _run(skill_server.edit(image_path="/tmp/x.png", prompt="make it blue"))
    assert "edited" in result[0].text
    kwargs = client.generate_content.call_args.kwargs
    assert kwargs["files"] == ["/safe/path.png"]
    assert "Edit this image: make it blue" in kwargs["prompt"]


def test_session_create_happy_path(monkeypatch):
    """_session_create 创建 session 并存入 _sessions，返回 'Session created: sess_N'。"""
    fake_session = SimpleNamespace(cid="c_new")
    client = SimpleNamespace(start_chat=lambda model: fake_session)
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    monkeypatch.setattr(skill_server, "_sessions", {})
    result = _run(skill_server.session(action="create", model="flash",
                                        thinking_level="standard", learning_mode="quiz"))
    assert "Session created: sess_1" in result[0].text
    assert "sess_1" in skill_server._sessions


def test_session_list_empty(monkeypatch):
    monkeypatch.setattr(skill_server, "_sessions", {})
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    result = _run(skill_server.session(action="list"))
    assert result[0].text == "No active sessions"


def test_session_list_renders(monkeypatch):
    monkeypatch.setattr(skill_server, "_sessions", {
        "sess_1": {"session": SimpleNamespace(), "model": "flash",
                   "thinking_level": "standard", "learning_mode": None},
        "sess_2": {"session": SimpleNamespace(), "model": "pro",
                   "thinking_level": "extended", "learning_mode": None},
    })
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    result = _run(skill_server.session(action="list"))
    assert "1. sess_1 (flash)" in result[0].text
    assert "2. sess_2 (pro)" in result[0].text


def test_session_reset_specific(monkeypatch):
    """reset 单个 session → 'Session deleted: {id}'，不调 reset_client。"""
    monkeypatch.setattr(skill_server, "_sessions", {"sess_1": {"session": SimpleNamespace()}})
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    reset_called = []
    monkeypatch.setattr(skill_server, "reset_client", lambda: reset_called.append(1))
    result = _run(skill_server.session(action="reset", session_id="sess_1"))
    assert result[0].text == "Session deleted: sess_1"
    assert "sess_1" not in skill_server._sessions
    assert reset_called == []  # 单个删除不调 reset_client


def test_session_reset_all(monkeypatch):
    """reset 无 session_id → 清空所有 + 调 reset_client。"""
    monkeypatch.setattr(skill_server, "_sessions", {
        "sess_1": {"session": SimpleNamespace()},
        "sess_2": {"session": SimpleNamespace()},
    })
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    reset_called = []
    monkeypatch.setattr(skill_server, "reset_client", lambda: reset_called.append(1))
    result = _run(skill_server.session(action="reset"))
    assert result[0].text == "All sessions reset"
    assert skill_server._sessions == {}
    assert reset_called == [1]


def test_session_invalid_action(monkeypatch):
    """未知 action → 'Invalid action'。"""
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    result = _run(skill_server.session(action="unknown"))
    assert result[0].text == "Invalid action"


def test_doctor_happy_path_markdown(monkeypatch):
    """doctor 调 _doctor_payload + _format_doctor_markdown 返回 markdown。"""
    monkeypatch.setattr(skill_server, "_doctor_payload",
                        lambda browser, validate_browser: {"overall": "ok"})
    monkeypatch.setattr(skill_server, "_format_doctor_markdown",
                        lambda p: "DOCTOR REPORT")
    result = _run(skill_server.doctor(browser="chrome", validate_browser=True))
    assert result[0].text == "DOCTOR REPORT"


def test_cleanup_happy_path(monkeypatch):
    """cleanup 调 _cleanup_test_artifacts_payload + _format_cleanup_markdown。"""
    client = SimpleNamespace()
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "_cleanup_test_artifacts_payload",
                        AsyncMock(return_value={"chats": []}))
    monkeypatch.setattr(skill_server, "_format_cleanup_markdown",
                        lambda p: "CLEANUP REPORT")
    result = _run(skill_server.cleanup(markers="codex-", dry_run=True))
    assert result[0].text == "CLEANUP REPORT"


def test_history_export_with_list_chats_match(monkeypatch):
    """export 时 list_chats 命中 chat_id → 用 _chat_to_dict 作为 metadata（lines 398-401）。"""
    chat_item = SimpleNamespace(title="Matched", cid="c_1")
    client = SimpleNamespace(list_chats=lambda: [chat_item], read_chat=AsyncMock())

    async def fake_read_turns(_client, _cid, _limit, _max_chars):
        return None, [{"role": "user", "text": "t"}]

    monkeypatch.setattr(skill_server, "_read_chat_turns", fake_read_turns)
    monkeypatch.setattr(skill_server, "_chat_to_dict",
                        lambda c: {"id": getattr(c, "cid", ""), "title": getattr(c, "title", "")})
    monkeypatch.setattr(skill_server, "_get_chat_id", lambda c: getattr(c, "cid", ""))
    monkeypatch.setattr(skill_server, "_chat_export_payload",
                        lambda cid, h, t, m, _limit, mc: {"meta": m, "turns": len(t)})
    monkeypatch.setattr(skill_server, "_format_chat_export_markdown",
                        lambda p: f"meta_title={p['meta']['title']}")
    _patch_client_seams(monkeypatch, client)
    result = _run(skill_server.history(action="export", chat_id="c_1"))
    assert result[0].text == "meta_title=Matched"


def test_cookie_status_happy_path(monkeypatch):
    """cookie action='status' 走 get_cookie_status → 渲染 'Cookie: OK'。"""
    monkeypatch.setattr(skill_server, "get_cookie_status",
                        lambda: {"has_cookie": True})
    result = _run(skill_server.cookie(action="status"))
    assert result[0].text == "Cookie: OK"


def test_cookie_get_happy_path(monkeypatch):
    """cookie action='get' 走 get_cookie_from_browser → 'Cookie: Loaded'。"""
    monkeypatch.setattr(skill_server, "get_cookie_from_browser",
                        lambda browser, profile: True)
    result = _run(skill_server.cookie(action="get", browser="chrome"))
    assert result[0].text == "Cookie: Loaded"


def test_cookie_get_with_profile(monkeypatch):
    monkeypatch.setattr(skill_server, "get_cookie_from_browser",
                        lambda browser, profile: False)
    result = _run(skill_server.cookie(action="get", browser="chrome", profile="Default"))
    assert result[0].text == "Cookie Default: Failed"


def test_cookie_profiles_happy_path(monkeypatch):
    monkeypatch.setattr(skill_server, "list_browser_cookie_profiles",
                        lambda browser, validate: [
                            {"profile": "Default", "has_psid": True,
                             "chrome_selected_profile": True, "account_available": True,
                             "scheduled_registry_count": 5},
                        ])
    result = _run(skill_server.cookie(action="profiles"))
    assert "Default" in result[0].text
    assert "psid=yes" in result[0].text
    assert "chrome_selected=yes" in result[0].text
    assert "account_available=yes" in result[0].text


def test_cookie_profiles_empty(monkeypatch):
    monkeypatch.setattr(skill_server, "list_browser_cookie_profiles",
                        lambda browser, validate: [])
    result = _run(skill_server.cookie(action="profiles"))
    assert result[0].text == "No profiles"


def test_cookie_invalid_action(monkeypatch):
    """直接调用绕过 Literal 校验，未知 action → 'Invalid action'。"""
    result = _run(skill_server.cookie(action="unknown"))
    assert result[0].text == "Invalid action"


# ---------------------------------------------------------------------------
# M. 主入口 dispatch 行覆盖（scheduled main 758-765 / session main 945-951）
# ---------------------------------------------------------------------------


def test_scheduled_main_dispatches_list(monkeypatch):
    """scheduled(action='list') 走主入口 → _scheduled_list（line 759）。"""
    client = SimpleNamespace(_batch_execute=AsyncMock())
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "_fetch_scheduled_registry",
                        AsyncMock(return_value=([{"title": "T", "id": "s1"}], {})))
    result = _run(skill_server.scheduled(action="list"))
    assert "T (s1)" in result[0].text


def test_scheduled_main_dispatches_get(monkeypatch):
    """scheduled(action='get') 走主入口 → _scheduled_get（line 761）。"""
    client = SimpleNamespace(_batch_execute=AsyncMock())
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "_fetch_scheduled_task_by_id",
                        AsyncMock(return_value=({"id": "s1", "title": "T", "enabled": True}, {})))
    result = _run(skill_server.scheduled(action="get", action_id="s1"))
    assert "T" in result[0].text


def test_scheduled_main_dispatches_create(monkeypatch):
    """scheduled(action='create') 走主入口 → _scheduled_create（line 763）。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [[["x"]]])
    monkeypatch.setattr(skill_server, "_parse_scheduled_action_create_body", lambda b: {"id": ""})
    monkeypatch.setattr(skill_server, "_scheduled_daily_payload", lambda *a: "p")
    result = _run(skill_server.scheduled(action="create", title="T", instructions="I", hour=9))
    assert "Created: T" in result[0].text


def test_scheduled_main_dispatches_delete(monkeypatch):
    """scheduled(action='delete') 走主入口 → _scheduled_delete（line 765）。"""
    client = SimpleNamespace(_batch_execute=AsyncMock(return_value=_ns(text="t")))
    _patch_client_seams(monkeypatch, client)
    monkeypatch.setattr(skill_server, "_extract_rpc_bodies", lambda _t, _r: [])
    result = _run(skill_server.scheduled(action="delete", action_id="s1"))
    assert "rpc_unconfirmed" in result[0].text


def test_session_main_invalid_image_early_return(monkeypatch):
    """session 主入口 validate_optional_image_path 失败早退（line 946）。"""
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (False, None, "bad image"))
    result = _run(skill_server.session(action="list", image_path="/bad"))
    assert result[0].text == "Error: bad image"


def test_session_main_dispatches_send(monkeypatch):
    """session(action='send') 走主入口 → _session_send（line 951）。"""
    fake_session = SimpleNamespace(
        send_message=AsyncMock(return_value=_ns(text="sent")),
        cid="c_s",
    )
    monkeypatch.setattr(skill_server, "_sessions", {
        "s1": {"session": fake_session, "model": "flash",
               "thinking_level": "standard", "learning_mode": None},
    })
    _patch_client_seams(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(skill_server, "validate_optional_image_path",
                        lambda _p: (True, None, None))
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup_from_response",
                        lambda _r, source: None)
    monkeypatch.setattr(skill_server, "schedule_remote_chat_cleanup",
                        lambda _cid, source: None)
    result = _run(skill_server.session(action="send", session_id="s1", message="hello"))
    assert "sent" in result[0].text
