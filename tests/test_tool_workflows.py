import asyncio
import json
import os
import sys
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP


def _tool_text(result):
    content, _meta = result
    return content[0].text


def test_parse_response_exposes_remote_chat_id_for_cleanup():
    from src.tools.utils import parse_response

    response = SimpleNamespace(
        text="ok",
        images=[],
        videos=[],
        media=[],
        metadata=["c_cleanup123", "r_response"],
    )

    assert "Remote chat ID: c_cleanup123" in parse_response(response)[0].text


def test_skill_server_response_exposes_remote_chat_id_for_cleanup():
    import src.skill_server as skill_server

    response = SimpleNamespace(
        text="ok",
        images=[],
        videos=[],
        media=[],
        metadata=["c_skill_cleanup", "r_response"],
    )

    assert "Remote chat ID: c_skill_cleanup" in skill_server._format_response(response)[0].text


def test_all_group_registers_unique_complete_tool_surface():
    from src.tools import register_tools

    async def run():
        mcp = FastMCP("test")
        register_tools(mcp, ["all"])
        tools = await mcp.list_tools()
        names = [tool.name for tool in tools]

        assert len(names) == len(set(names))
        assert "gemini_generate_media" in names
        assert "gemini_deep_research" in names
        assert "gemini_list_research_report_actions" in names
        assert "gemini_create_from_research_report" in names
        assert "gemini_upload_file" in names
        assert "gemini_probe_web_features" in names
        assert "gemini_get_web_capabilities" in names
        assert "gemini_get_tool_manifest" in names
        assert "gemini_list_scheduled_actions" in names
        assert "gemini_create_scheduled_action" in names
        assert "gemini_delete_scheduled_action" in names
        assert "gemini_get_tool_mode_status" in names
        assert "gemini_search_chats" in names
        assert "gemini_export_chat" in names
        assert "gemini_manage_prompts" not in names

    asyncio.run(run())


def test_all_group_tools_have_mcp_annotations():
    from src.tools import register_tools

    async def run():
        mcp = FastMCP("test")
        register_tools(mcp, ["all"])
        tools = await mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        assert all(tool.annotations is not None for tool in tools)
        assert by_name["gemini_chat"].annotations.readOnlyHint is False
        assert by_name["gemini_chat"].annotations.openWorldHint is True
        assert by_name["gemini_list_sessions"].annotations.readOnlyHint is True
        assert by_name["gemini_list_sessions"].annotations.openWorldHint is False
        assert by_name["gemini_reset_session"].annotations.destructiveHint is True
        assert by_name["gemini_upload_file"].annotations.openWorldHint is True
        assert by_name["gemini_deep_research"].annotations.readOnlyHint is False
        assert by_name["gemini_list_research_report_actions"].annotations.readOnlyHint is True
        assert by_name["gemini_create_from_research_report"].annotations.readOnlyHint is False
        assert by_name["gemini_create_from_research_report"].annotations.openWorldHint is False
        assert by_name["gemini_delete_chat"].annotations.destructiveHint is True
        assert by_name["gemini_create_scheduled_action"].annotations.readOnlyHint is False
        assert by_name["gemini_create_scheduled_action"].annotations.destructiveHint is False
        assert by_name["gemini_delete_scheduled_action"].annotations.destructiveHint is True

    asyncio.run(run())


def test_core_group_stays_focused_on_high_value_ai_tools():
    from src.tools import register_tools

    async def run():
        mcp = FastMCP("test")
        register_tools(mcp, ["core"])
        tools = await mcp.list_tools()
        names = {tool.name for tool in tools}

        assert "gemini_chat" in names
        assert "gemini_generate_media" in names
        assert "gemini_upload_file" in names
        assert "gemini_deep_research" in names
        assert "gemini_create_from_research_report" in names
        assert "gemini_manage_prompts" not in names
        assert "gemini_list_chats" not in names

    asyncio.run(run())


def test_deep_research_uses_default_transport_for_model_aliases(monkeypatch):
    import src.tools.research as research_tools

    calls = []

    class FakePlan:
        title = "Research plan"
        research_id = None
        cid = "c_research"
        response_text = "plan text"
        confirm_prompt = "Start research"

    class FakeOutput:
        text = "I am on it."

    class FakeClient:
        def start_chat(self, model=None):
            calls.append(("start_chat", model))
            return SimpleNamespace(cid="c_research")

        def thinking_scope(self, model, thinking_level):
            calls.append(("thinking_scope", model, thinking_level))
            return research_tools._null_scope()

        async def create_deep_research_plan(self, query, chat=None, model=None):
            calls.append(("create_plan", query, model))
            return FakePlan()

        async def start_deep_research(self, plan, chat=None):
            calls.append(("start_research", plan.cid))
            return FakeOutput()

        async def wait_for_deep_research(self, plan, poll_interval=10, timeout=600):
            raise AssertionError("missing research_id should use chat-history fallback")

        async def fetch_latest_chat_response(self, cid):
            calls.append(("fetch_latest", cid))
            return SimpleNamespace(text="final report with sources")

    async def noop_initialize():
        return None

    async def noop_cleanup(client=None):
        return 0

    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(research_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(research_tools, "cleanup_due_remote_chats", noop_cleanup)
    monkeypatch.setattr(research_tools, "schedule_remote_chat_cleanup", lambda *args, **kwargs: None)

    async def run():
        mcp = FastMCP("test")
        research_tools.register_research_tools(mcp)
        result = await mcp.call_tool(
            "gemini_deep_research",
            {
                "query": "research topic",
                "model": "pro",
                "timeout_seconds": 30,
                "poll_interval_seconds": 3,
            },
        )
        text = _tool_text(result)
        assert "请求模型: pro" in text
        assert "Gemini Web default Deep Research mode" in text
        assert "final report with sources" in text

    asyncio.run(run())

    start_call = next(call for call in calls if call[0] == "start_chat")
    create_call = next(call for call in calls if call[0] == "create_plan")
    assert getattr(start_call[1], "model_name", None) == "unspecified"
    assert getattr(create_call[2], "model_name", None) == "unspecified"
    assert "Requested MCP model alias: pro" in create_call[1]


def test_deep_research_clears_stale_default_chat_metadata():
    import src.tools.research as research_tools

    class FakeClient:
        def start_chat(self, model=None):
            return SimpleNamespace(cid="c_old", rid="r_old", rcid="rc_old", model=model)

    chat = research_tools._start_fresh_research_chat(FakeClient(), model="unspecified")

    assert chat.cid == ""
    assert chat.rid == ""
    assert chat.rcid == ""


def test_deep_research_extracts_immersive_report_and_sources():
    import json
    import src.tools.research as research_tools

    report = "# Research Report\n\n" + ("Body with [cite: 1]. " * 80)
    source = [
        "https://t0.gstatic.com/faviconV2?url=https%3A%2F%2Fexample.com",
        "https://example.com/report",
        "Example report",
    ]
    citation_map = [{"44": [[[[" [cite: 1]"], [[None, None, None, [source, 1]]]]]]}]
    candidate = [
        "rc_1",
        ["我已经完成了研究。\n\nhttp://googleusercontent.com/immersive_entry_chip/0"],
    ] + [None] * 28 + [
        [
            [
                "im_1",
                None,
                "Title",
                "task",
                report,
                [],
                None,
                None,
                [],
                "im_1",
                3,
                None,
                False,
                None,
                None,
                None,
                None,
                citation_map,
            ]
        ]
    ]
    payload = [["wrb.fr", "hNvQHb", json.dumps([[[None, None, None, [[candidate]]]]])]]

    extracted = research_tools._extract_deep_research_immersive_report(payload)

    assert extracted["report"] == report
    assert extracted["sources"] == [{"url": "https://example.com/report", "title": "Example report"}]
    assert extracted["title"] == "Title"
    assert extracted["immersive_id"] == "im_1"


def test_research_report_actions_and_artifact_creation(monkeypatch, tmp_path):
    import src.tools.research as research_tools

    report = (
        "# AI Podcast Workflow\n\n"
        "The report says AI production systems can connect research, scripting, and publishing. [cite: 1]\n\n"
        "## Research Pipeline\n\n"
        "Agents collect sources, compare evidence, and keep citations attached.\n\n"
        "## Publishing\n\n"
        "Teams can turn the report into a landing page and show notes."
    )
    report_output = SimpleNamespace(
        report=report,
        text=report,
        title="AI Podcast Workflow",
        immersive_id="im_test",
        sources=[{"url": "https://example.com/report", "title": "Example report"}],
    )

    async def fake_initialize_client():
        return None

    async def fake_fetch_report(client, chat_id):
        assert chat_id == "c_report"
        return report_output

    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: object())
    monkeypatch.setattr(research_tools, "initialize_client", fake_initialize_client)
    monkeypatch.setattr(research_tools, "_fetch_deep_research_immersive_report", fake_fetch_report)

    async def run():
        mcp = FastMCP("test")
        research_tools.register_research_tools(mcp)

        actions_result = await mcp.call_tool(
            "gemini_list_research_report_actions",
            {"chat_id": "c_report", "response_format": "json"},
        )
        actions_payload = json.loads(_tool_text(actions_result))
        assert actions_payload["native_web_menu_observed"] is True
        assert {item["type"] for item in actions_payload["actions"]} >= {
            "webpage",
            "infographic",
            "quiz",
            "flashcards",
            "audio_overview",
            "custom_app",
        }

        artifact_result = await mcp.call_tool(
            "gemini_create_from_research_report",
            {
                "chat_id": "c_report",
                "artifact_type": "webpage",
                "output_dir": str(tmp_path),
                "response_format": "json",
            },
        )
        artifact = json.loads(_tool_text(artifact_result))
        assert artifact["artifact_type"] == "webpage"
        assert artifact["native_web_menu_observed"] is True
        assert artifact["source_count"] == 1
        with open(artifact["path"], encoding="utf-8") as f:
            html = f.read()
        assert "AI Podcast Workflow" in html
        assert "https://example.com/report" in html
        assert 'rel="noopener noreferrer"' in html

    asyncio.run(run())


def test_research_artifact_helpers_handle_edge_cases():
    import src.tools.research as research_tools

    assert research_tools._plain_excerpt("# Heading\n[Link](https://example.com) `code` [cite: 1]", 0) == ""
    assert research_tools._plain_excerpt("abcdef", 1) == "…"
    assert research_tools._plain_excerpt("abcdef", 4) == "abc…"

    links = research_tools._iter_source_links(
        [
            {"url": "javascript:alert(1)", "title": "bad"},
            {"title": "missing url"},
            {"url": "https://example.com/a", "title": ""},
            {"url": "http://example.com/b", "title": "B"},
        ],
        limit=10,
    )
    assert links == [
        ("https://example.com/a", "https://example.com/a"),
        ("http://example.com/b", "B"),
    ]


def test_prompts_group_tool_has_local_destructive_annotation():
    from src.tools import register_tools

    async def run():
        mcp = FastMCP("test")
        register_tools(mcp, ["prompts"])
        tools = await mcp.list_tools()
        tool = next(item for item in tools if item.name == "gemini_manage_prompts")
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.openWorldHint is False

    asyncio.run(run())


def test_paginate_items_normalizes_invalid_inputs():
    import src.tools.manage as manage_tools

    page, pagination = manage_tools._paginate_items([1, 2, 3], limit="bad", offset=-4, max_limit=2)

    assert page == [1, 2]
    assert pagination == {
        "total_count": 3,
        "count": 2,
        "offset": 0,
        "limit": 2,
        "has_more": True,
        "next_offset": 2,
    }


def test_prompt_list_exposes_full_id_for_cleanup(tmp_path):
    import src.tools.prompts as prompt_tools

    prompt_tools._prompt_manager = prompt_tools.PromptManager(str(tmp_path / "prompts.json"))

    async def run():
        mcp = FastMCP("test")
        prompt_tools.register_prompts_tools(mcp)
        created = await mcp.call_tool(
            "gemini_manage_prompts",
            {
                "action": "create",
                "name": "cleanup prompt",
                "content": "temporary",
                "category": "test",
            },
        )
        prompt_id = _tool_text(created).split("ID: ", 1)[1].splitlines()[0]
        listed = await mcp.call_tool("gemini_manage_prompts", {"action": "list"})

        assert f"ID: {prompt_id}" in _tool_text(listed)
        assert f"ID: {prompt_id[:8]}..." not in _tool_text(listed)

    try:
        asyncio.run(run())
    finally:
        prompt_tools._prompt_manager = None


def test_server_utility_tools_have_annotations():
    import src.server as server

    async def run():
        tools = await server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        assert "gemini_get_tool_manifest" in by_name
        assert by_name["gemini_get_tool_manifest"].annotations.readOnlyHint is True
        assert by_name["gemini_get_tool_manifest"].annotations.openWorldHint is False
        assert by_name["gemini_reset"].annotations.readOnlyHint is False
        assert by_name["gemini_reset"].annotations.openWorldHint is False
        assert by_name["gemini_get_cookie_status"].annotations.readOnlyHint is True
        assert by_name["gemini_list_browser_cookie_profiles"].annotations.readOnlyHint is True
        assert by_name["gemini_list_browser_cookie_profiles"].annotations.openWorldHint is False
        assert by_name["gemini_get_cookie_from_browser"].annotations.readOnlyHint is False
        assert by_name["gemini_get_cookie_from_browser"].annotations.openWorldHint is False

    asyncio.run(run())


def test_skill_server_tools_have_mcp_annotations():
    import src.skill_server as skill_server

    async def run():
        tools = await skill_server.mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}

        assert all(tool.annotations is not None for tool in tools)
        assert by_name["chat"].annotations.readOnlyHint is False
        assert by_name["chat"].annotations.openWorldHint is True
        assert by_name["history"].annotations.destructiveHint is True
        assert by_name["history"].annotations.openWorldHint is True
        assert by_name["account"].annotations.readOnlyHint is True
        assert by_name["account"].annotations.openWorldHint is True
        assert by_name["session"].annotations.destructiveHint is True
        assert by_name["prompts"].annotations.destructiveHint is True
        assert by_name["prompts"].annotations.openWorldHint is False
        assert by_name["cookie"].annotations.readOnlyHint is False
        assert by_name["cookie"].annotations.openWorldHint is False

    asyncio.run(run())


def test_file_validation_runs_before_client_initialization(monkeypatch):
    import src.tools.file as file_tools

    def fail_client():
        raise AssertionError("client should not initialize for invalid input")

    async def fail_initialize():
        raise AssertionError("client should not initialize for invalid input")

    monkeypatch.setattr(file_tools, "get_gemini_client", fail_client)
    monkeypatch.setattr(file_tools, "initialize_client", fail_initialize)

    async def run():
        mcp = FastMCP("test")
        file_tools.register_file_tools(mcp)

        path_result = await mcp.call_tool("gemini_upload_file", {"file_path": "../secret.txt"})
        assert "路径遍历" in _tool_text(path_result)

        url_result = await mcp.call_tool("gemini_analyze_url", {"url": "not-a-url"})
        assert "URL 格式无效" in _tool_text(url_result)

    asyncio.run(run())


def test_gem_management_uses_current_gemini_webapi_contract(monkeypatch):
    import src.tools.manage as manage_tools

    calls = []

    class FakeClient:
        async def fetch_gems(self):
            return {
                "gem-1": SimpleNamespace(
                    id="gem-1",
                    name="Helper",
                    description="A useful helper",
                )
            }

        async def create_gem(self, name, prompt, description=""):
            calls.append(("create", name, prompt, description))
            return SimpleNamespace(id="gem-created")

        async def update_gem(self, gem, name, prompt, description=""):
            calls.append(("update", gem, name, prompt, description))

        async def delete_gem(self, gem):
            calls.append(("delete", gem))

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        list_result = await mcp.call_tool("gemini_manage_gems", {"action": "list"})
        assert "Helper" in _tool_text(list_result)

        create_result = await mcp.call_tool(
            "gemini_manage_gems",
            {
                "action": "create",
                "name": "Writer",
                "description": "Drafts copy",
                "instructions": "Write clearly.",
            },
        )
        assert "gem-created" in _tool_text(create_result)

        update_result = await mcp.call_tool(
            "gemini_manage_gems",
            {
                "action": "update",
                "gem_id": "gem-created",
                "name": "Writer",
                "description": "Drafts better copy",
                "instructions": "Write warmly.",
            },
        )
        assert "更新成功" in _tool_text(update_result)

        await mcp.call_tool("gemini_manage_gems", {"action": "delete", "gem_id": "gem-created"})

    asyncio.run(run())

    assert calls == [
        ("create", "Writer", "Write clearly.", "Drafts copy"),
        ("update", "gem-created", "Writer", "Write warmly.", "Drafts better copy"),
        ("delete", "gem-created"),
    ]


def test_stream_tools_use_text_delta(monkeypatch):
    import src.tools.chat as chat_tools

    class FakeStreamResponse:
        def __init__(self, text, text_delta):
            self.text = text
            self.text_delta = text_delta
            self.images = []
            self.videos = []
            self.media = []

    class FakeSession:
        async def send_message_stream(self, prompt, files=None, temporary=False, thinking_level=None):
            yield FakeStreamResponse("SESSION_", "SESSION_")
            yield FakeStreamResponse("SESSION_STREAM_OK", "STREAM_OK")

    class FakeClient:
        async def generate_content_stream(
            self, prompt, files=None, model=None, gem=None, temporary=False, thinking_level=None
        ):
            yield FakeStreamResponse("STREAM_", "STREAM_")
            yield FakeStreamResponse("STREAM_OK", "OK")
            yield FakeStreamResponse("STREAM_OK", "")

    async def noop_initialize():
        return None

    monkeypatch.setattr(chat_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(chat_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(
        chat_tools,
        "get_session",
        lambda session_id: {"session": FakeSession(), "model": "fast"},
    )

    async def run():
        mcp = FastMCP("test")
        chat_tools.register_chat_tools(mcp)

        stream_result = await mcp.call_tool(
            "gemini_chat_stream",
            {"message": "ignored", "model": "fast"},
        )
        assert _tool_text(stream_result) == "STREAM_OK"

        session_stream_result = await mcp.call_tool(
            "gemini_send_message_stream",
            {"session_id": "session-1", "message": "ignored"},
        )
        assert _tool_text(session_stream_result) == "SESSION_STREAM_OK"

    asyncio.run(run())


def test_chat_tools_forward_gem_and_temporary_chat_settings(monkeypatch):
    import src.tools.chat as chat_tools

    calls = []

    class FakeResponse:
        text = "ok"
        images = []
        videos = []
        media = []
        metadata = []

    class FakeSession:
        cid = "c_temporary"

        async def send_message(self, prompt, files=None, temporary=False, thinking_level=None):
            calls.append(("send", prompt, files, temporary, thinking_level))
            return FakeResponse()

    class FakeClient:
        async def generate_content(
            self, prompt, files=None, model=None, gem=None, temporary=False, thinking_level=None
        ):
            calls.append(("chat", prompt, files, model, gem, temporary, thinking_level))
            return FakeResponse()

        def start_chat(self, model=None, gem=None):
            calls.append(("start", model, gem))
            return FakeSession()

    async def noop_initialize():
        return None

    async def noop_cleanup(client=None):
        return 0

    monkeypatch.setattr(chat_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(chat_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(chat_tools, "cleanup_due_remote_chats", noop_cleanup)
    monkeypatch.setattr(chat_tools, "store_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        chat_tools,
        "get_session",
        lambda session_id: {
            "session": FakeSession(),
            "model": "fast",
            "temporary": True,
        },
    )

    async def run():
        mcp = FastMCP("test")
        chat_tools.register_chat_tools(mcp)

        await mcp.call_tool(
            "gemini_chat",
            {
                "message": "Use the gem.",
                "model": "fast",
                "gem_id": "gem-writer",
                "temporary": True,
            },
        )
        await mcp.call_tool(
            "gemini_start_chat",
            {
                "model": "thinking",
                "gem_id": "gem-research",
                "temporary": True,
            },
        )
        await mcp.call_tool(
            "gemini_send_message",
            {
                "session_id": "session-1",
                "message": "Keep this temporary.",
            },
        )

    asyncio.run(run())

    assert calls == [
        ("chat", "Use the gem.", None, "gemini-3-flash", "gem-writer", True, "standard"),
        ("start", "gemini-3-flash-thinking", "gem-research"),
        ("send", "Keep this temporary.", None, True, "standard"),
    ]


def test_chat_tool_accepts_runtime_model_name(monkeypatch):
    import src.tools.chat as chat_tools

    calls = []

    class FakeResponse:
        text = "ok"
        images = []
        videos = []
        media = []
        metadata = []

    class FakeClient:
        async def generate_content(
            self, prompt, files=None, model=None, gem=None, temporary=False, thinking_level=None
        ):
            calls.append(model)
            return FakeResponse()

    async def noop_initialize():
        return None

    async def noop_cleanup(client=None):
        return 0

    monkeypatch.setattr(chat_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(chat_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(chat_tools, "cleanup_due_remote_chats", noop_cleanup)

    async def run():
        mcp = FastMCP("test")
        chat_tools.register_chat_tools(mcp)
        await mcp.call_tool(
            "gemini_chat",
            {
                "message": "Use live model.",
                "model": "3.5 Flash",
            },
        )

    asyncio.run(run())

    assert calls == ["3.5 Flash"]


def test_thinking_level_transport_extends_stream_generate_payload():
    import orjson

    from src.thinking_client import inject_thinking_level

    inner_request = [None] * 69
    inner_request[0] = ["prompt", 0]
    request_data = {
        "at": "token",
        "f.req": orjson.dumps(
            [None, orjson.dumps(inner_request).decode("utf-8")]
        ).decode("utf-8"),
    }

    patched = inject_thinking_level(request_data, mode_id=6, level_id=2)
    outer_request = orjson.loads(patched["f.req"])
    patched_inner = orjson.loads(outer_request[1])

    assert len(patched_inner) == 81
    assert patched_inner[79] == 6
    assert patched_inner[80] == 2
    assert request_data["f.req"] != patched["f.req"]


def test_web_request_transport_injects_learning_companion_fields():
    import orjson

    from src.thinking_client import WebRequestOptions, inject_web_request_options

    inner_request = [None] * 69
    inner_request[0] = ["prompt", 0]
    request_data = {
        "at": "token",
        "f.req": orjson.dumps(
            [None, orjson.dumps(inner_request).decode("utf-8")]
        ).decode("utf-8"),
    }

    patched = inject_web_request_options(
        request_data,
        WebRequestOptions(
            thinking_mode_id=3,
            thinking_level_id=2,
            learning_mode_id=18,
            learning_x9b_field="h5d",
            learning_x9b_value=1,
        ),
    )
    outer_request = orjson.loads(patched["f.req"])
    patched_inner = orjson.loads(outer_request[1])

    assert patched_inner[54] == [[[None, None, None, [1]]]]
    assert patched_inner[55] == [[18]]
    assert patched_inner[79] == 3
    assert patched_inner[80] == 2


def test_learning_mode_prompt_prefix_matches_web_ui_selection():
    from src.thinking_client import ThinkingLevelGeminiClient

    client = object.__new__(ThinkingLevelGeminiClient)

    args, kwargs = client._with_learning_prompt(
        (),
        {"prompt": "光合作用"},
        "quiz",
    )

    assert args == ()
    assert kwargs["prompt"] == "生成一份关于以下内容的互动式测验： 光合作用"


def test_current_web_models_resolve_thinking_mode_buckets():
    from src.constants import (
        resolve_learning_mode_config,
        resolve_media_request,
        resolve_thinking_level_id,
        resolve_thinking_mode_id,
    )

    assert resolve_thinking_mode_id("3.1 Flash-Lite") == 6
    assert resolve_thinking_mode_id("3.5 Flash") == 1
    assert resolve_thinking_mode_id("3.1 Pro") == 3
    assert resolve_thinking_level_id("standard") == 1
    assert resolve_thinking_level_id("extended") == 2
    assert resolve_media_request("flash-lite", "image")["backend_label"] == "Nano Banana 2"
    assert resolve_media_request("flash-lite", "music", "standard")["backend_label"] == "Lyria 3"
    assert resolve_media_request("flash", "music", "extended")["request_model"] == "gemini-3-flash"
    assert resolve_media_request("flash", "music", "extended")["backend_label"] == "Lyria 3"
    assert resolve_media_request("pro", "music", "extended")["backend_label"] == "Lyria 3 Pro"
    assert resolve_learning_mode_config("quiz")["id"] == 18
    assert resolve_learning_mode_config("study-guide")["x9b_value"] == 4


def test_skill_server_uses_v2_file_attachment_contract(monkeypatch):
    import src.skill_server as skill_server

    calls = []

    class FakeResponse:
        text = "ok"
        images = []
        videos = []

    class FakeClient:
        async def generate_content(self, prompt, files=None, model=None, thinking_level=None):
            calls.append((prompt, files, model))
            return FakeResponse()

    async def noop_initialize():
        return None

    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(skill_server, "initialize_client", noop_initialize)

    async def run():
        result = await skill_server.edit(
            image_path="/tmp/reference.png",
            prompt="make it brighter",
            model="pro",
        )
        assert result[0].text == "ok"

    asyncio.run(run())

    assert calls == [
        (
            "Edit this image: make it brighter",
            ["/tmp/reference.png"],
            "gemini-3-pro",
        )
    ]


def test_skill_server_create_routes_current_media_backends(monkeypatch):
    import src.skill_server as skill_server

    calls = []

    class FakeResponse:
        text = "ok"
        images = []
        videos = []

    class FakeClient:
        async def generate_content(self, prompt, files=None, model=None, thinking_level=None):
            calls.append((prompt, files, model, thinking_level))
            return FakeResponse()

    async def noop_initialize():
        return None

    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(skill_server, "initialize_client", noop_initialize)

    async def run():
        image_result = await skill_server.create(
            prompt="studio portrait",
            type="image",
            model="pro",
        )
        music_result = await skill_server.create(
            prompt="cinematic trailer",
            type="music",
            model="pro",
            thinking_level="extended",
        )
        assert "Backend: Nano Banana 2" in image_result[0].text
        assert "Backend: Lyria 3 Pro" in music_result[0].text

    asyncio.run(run())

    assert calls == [
        ("Generate image: studio portrait", None, "gemini-3-flash", "standard"),
        ("Create music: cinematic trailer", None, "gemini-3-pro", "extended"),
    ]


def test_skill_server_account_and_history_tools(monkeypatch):
    import src.skill_server as skill_server

    deleted = []

    class FakeClient:
        def __init__(self):
            self.deleted_scheduled_ids = set()

        def list_models(self):
            return [
                SimpleNamespace(
                    display_name="Pro",
                    model_name="gemini-3-pro",
                    is_available=True,
                )
            ]

        async def inspect_account_status(self):
            return {"summary": {"deep_research_feature_present": True}}

        def list_chats(self):
            return [SimpleNamespace(cid="c_1", title="Chat one")]

        async def read_chat(self, chat_id, limit=10):
            return SimpleNamespace(
                turns=[
                    SimpleNamespace(role="user", text="hello"),
                    SimpleNamespace(role="model", text="world"),
                ]
            )

        async def delete_chat(self, chat_id):
            deleted.append(chat_id)

        async def _batch_execute(self, payloads, source_path="/app", close_on_error=True):
            rpcid = payloads[0].serialize()[0]
            if rpcid == "K4WWud":
                body = [["p1", "Shared chat", False, None, "https://gemini.google.com/share/abc"]]
            elif rpcid == "qpEbW":
                body = [[[[None, 11], 2, 3, [1781794431, 0], 100, 80]], ""]
            elif rpcid == "cYRIkd":
                body = [[[["canvas"], "Canvas", "Create and edit documents", ""]]]
            elif rpcid == "MaZiqc":
                body = [None, None, [["task-1", "Morning brief", True, None, None, [1781794431, 0], None, "Daily", None, 2]]]
            elif rpcid == "XPSWpd":
                body = [[
                    [
                        "sched-1",
                        [
                            ["Send morning brief", "Every day at 9 AM", "Morning brief"],
                            None,
                            [[[[9]], None, None, None, [1, 4], None, None, ["Asia/Shanghai"]]],
                            ["c_task", "en", "gem"],
                            None,
                            [True, True],
                        ],
                        [None, None, None, None, [1], [1781794431, 0], [1781794431, 0]],
                    ]
                ]]
            elif rpcid == "Jba3ib":
                body = [["skill-task", ["skill-task", [["Skill instructions", "Every day by 7 AM", "Skill title"]]]]]
            elif rpcid == "kwDCne":
                task_metadata = [None, None, None, [], None, None, [1781794431, 0]]
                if "skill-task" not in self.deleted_scheduled_ids:
                    task_metadata = [None, [1], None, None, None, [1781794431, 0], [1781794431, 0]]
                body = [[
                    "skill-task",
                    [
                        ["Skill instructions", "Every day by 7 AM", "Skill title"],
                        None,
                        [[[[7]], None, None, None, [1, 4], None, None, ["Asia/Shanghai"]]],
                        ["c_task", "zh-CN", "gem"],
                        None,
                        [True],
                    ],
                    task_metadata,
                ]]
            elif rpcid == "Q4Gw3c":
                payload = json.loads(payloads[0].serialize()[1])
                for item in payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []:
                    self.deleted_scheduled_ids.add(item)
                body = [[]]
            elif rpcid == "MyzX6c":
                body = [True, [[1, True, 1, 0, None, 1], [35, True, 0, 0, None, 0]]]
            else:
                body = []
            return SimpleNamespace(
                status_code=200,
                text=json.dumps([["wrb.fr", rpcid, json.dumps(body), None, None, None, "generic"]]),
            )

    async def noop_initialize():
        return None

    monkeypatch.setattr(skill_server, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(skill_server, "initialize_client", noop_initialize)

    async def run():
        account_result = await skill_server.account("status")
        assert "deep_research_feature_present" in account_result[0].text

        models_result = await skill_server.account("models")
        assert "gemini-3-pro" in models_result[0].text

        features_result = await skill_server.account("features")
        assert "library.library_index: ok" in features_result[0].text

        capabilities_result = await skill_server.account("capabilities")
        assert "3.1 Pro" in capabilities_result[0].text
        assert "gemini_probe_web_features" in capabilities_result[0].text
        assert "gemini_get_tool_manifest" in capabilities_result[0].text

        manifest_result = await skill_server.account("manifest")
        assert "Gemini MCP Tool Manifest" in manifest_result[0].text
        assert "gemini_delete_chat" in manifest_result[0].text

        links_result = await skill_server.account("links")
        assert "Shared chat" in links_result[0].text

        usage_result = await skill_server.account("usage")
        assert "remaining=80" in usage_result[0].text

        library_result = await skill_server.account("library")
        assert "Canvas" in library_result[0].text

        scheduled_result = await skill_server.account("scheduled")
        assert "Morning brief" in scheduled_result[0].text

        scheduled_list_result = await skill_server.scheduled("list")
        assert "Morning brief" in scheduled_list_result[0].text

        scheduled_get_result = await skill_server.scheduled(
            "get",
            action_id="skill-task",
        )
        assert "Skill title" in scheduled_get_result[0].text
        assert "state=running" in scheduled_get_result[0].text

        scheduled_create_result = await skill_server.scheduled(
            "create",
            title="Skill title",
            instructions="Skill instructions",
            hour=7,
        )
        assert "Created: skill-task" in scheduled_create_result[0].text

        scheduled_delete_result = await skill_server.scheduled(
            "delete",
            action_id="skill-task",
        )
        assert "Delete requested: skill-task" in scheduled_delete_result[0].text
        assert "deleted_state_by_id" in scheduled_delete_result[0].text

        modes_result = await skill_server.account("modes")
        assert "mode_id=1" in modes_result[0].text
        assert "mode_id=35" in modes_result[0].text

        list_result = await skill_server.history("list")
        assert "c_1" in list_result[0].text

        read_result = await skill_server.history("read", chat_id="c_1", limit=2)
        assert "hello" in read_result[0].text
        assert "world" in read_result[0].text

        search_result = await skill_server.history("search", query="Chat")
        assert "Chat one" in search_result[0].text

        export_result = await skill_server.history("export", chat_id="c_1", limit=2)
        assert "Gemini Chat Export" in export_result[0].text
        assert "hello" in export_result[0].text

        delete_result = await skill_server.history("delete", chat_id="c_1")
        assert "Deleted: c_1" in delete_result[0].text

    asyncio.run(run())
    assert deleted == ["c_1"]


def test_skill_server_static_account_actions_do_not_require_auth(monkeypatch):
    import src.skill_server as skill_server

    def fail_get_client():
        raise AssertionError("static account actions must not initialize Gemini client")

    async def fail_initialize():
        raise AssertionError("static account actions must not initialize Gemini client")

    monkeypatch.setattr(skill_server, "get_gemini_client", fail_get_client)
    monkeypatch.setattr(skill_server, "initialize_client", fail_initialize)

    async def run():
        capabilities_result = await skill_server.account("capabilities")
        assert "3.1 Pro" in capabilities_result[0].text

        manifest_result = await skill_server.account("manifest")
        assert "Gemini MCP Tool Manifest" in manifest_result[0].text

    asyncio.run(run())


def test_model_listing_prefers_runtime_registry(monkeypatch):
    import src.tools.manage as manage_tools

    class FakeClient:
        def list_models(self):
            return [
                SimpleNamespace(
                    display_name="Pro",
                    model_name="gemini-3-pro",
                    is_available=True,
                    description="High capability model",
                )
            ]

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)
        result = await mcp.call_tool("gemini_list_models", {})
        text = _tool_text(result)
        assert "MCP 模型别名" in text
        assert "Pro: gemini-3-pro (可用)" in text
        assert "High capability model" in text

    asyncio.run(run())


def test_chat_listing_uses_current_chat_cid(monkeypatch):
    import src.tools.manage as manage_tools

    class FakeClient:
        def list_chats(self):
            return [SimpleNamespace(cid="c_latest", title="Latest chat", is_pinned=True, timestamp=1760000000)]

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)
        result = await mcp.call_tool("gemini_list_chats", {})
        text = _tool_text(result)
        assert "Latest chat" in text
        assert "c_latest" in text
        assert "📌" in text

        json_result = await mcp.call_tool(
            "gemini_list_chats",
            {"response_format": "json", "limit": 1, "offset": 0},
        )
        assert '"total_count": 1' in _tool_text(json_result)

    asyncio.run(run())


def test_account_and_chat_management_tools_use_current_webapi_contract(monkeypatch):
    import src.tools.manage as manage_tools

    deleted = []

    class FakeClient:
        async def inspect_account_status(self):
            return {
                "source_path": "/app",
                "account_path": "",
                "summary": {"deep_research_feature_present": True},
                "rpc": {
                    "activity": {
                        "ok": True,
                        "status_code": 200,
                        "raw_preview": "should not be exposed",
                    }
                },
            }

        async def read_chat(self, chat_id, limit=20):
            assert chat_id == "c_latest"
            assert limit == 2
            return SimpleNamespace(
                cid=chat_id,
                turns=[
                    SimpleNamespace(role="user", text="hello"),
                    SimpleNamespace(role="model", text="world"),
                ],
            )

        async def delete_chat(self, chat_id):
            deleted.append(chat_id)

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        account_result = await mcp.call_tool("gemini_inspect_account", {})
        account_text = _tool_text(account_result)
        assert "deep_research_feature_present: True" in account_text
        assert "raw_preview" not in account_text

        read_result = await mcp.call_tool(
            "gemini_read_chat",
            {"chat_id": "c_latest", "limit": 2},
        )
        read_text = _tool_text(read_result)
        assert "hello" in read_text
        assert "world" in read_text

        delete_result = await mcp.call_tool("gemini_delete_chat", {"chat_id": "c_latest"})
        assert "已删除聊天" in _tool_text(delete_result)

    asyncio.run(run())
    assert deleted == ["c_latest"]


def test_chat_search_defaults_to_metadata_without_reading_turns(monkeypatch):
    import src.tools.manage as manage_tools

    class FakeClient:
        def list_chats(self):
            return [
                SimpleNamespace(cid="c_1", title="Project Alpha", is_pinned=False, timestamp=1760000000),
                SimpleNamespace(cid="c_2", title="Beta notes", is_pinned=False, timestamp=1760000100),
            ]

        async def read_chat(self, chat_id, limit=20):
            raise AssertionError("read_chat should not be called unless scan_turns is true")

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        result = await mcp.call_tool(
            "gemini_search_chats",
            {"query": "alpha", "response_format": "json"},
        )
        payload = json.loads(_tool_text(result))
        assert payload["scan_turns"] is False
        assert payload["match_count"] == 1
        assert payload["matches"][0]["id"] == "c_1"
        assert payload["matches"][0]["matched_fields"] == ["title"]
        assert "snippets" not in payload["matches"][0]

    asyncio.run(run())


def test_chat_search_can_scan_turns_with_truncated_snippets(monkeypatch):
    import src.tools.manage as manage_tools

    calls = []

    class FakeClient:
        def list_chats(self):
            return [
                SimpleNamespace(cid="c_1", title="Work log", is_pinned=False, timestamp=1760000000),
                SimpleNamespace(cid="c_2", title="Travel", is_pinned=False, timestamp=1760000100),
            ]

        async def read_chat(self, chat_id, limit=20):
            calls.append((chat_id, limit))
            if chat_id == "c_2":
                return SimpleNamespace(
                    cid=chat_id,
                    turns=[
                        SimpleNamespace(role="user", text="Find hotel options"),
                        SimpleNamespace(role="model", text="needle " + ("x" * 2000)),
                    ],
                )
            return SimpleNamespace(cid=chat_id, turns=[SimpleNamespace(role="user", text="nothing here")])

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        result = await mcp.call_tool(
            "gemini_search_chats",
            {
                "query": "needle",
                "scan_turns": True,
                "turns_per_chat": 3,
                "max_chars_per_turn": 120,
                "response_format": "json",
            },
        )
        payload = json.loads(_tool_text(result))
        assert payload["scan_turns"] is True
        assert payload["scanned_count"] == 2
        assert payload["match_count"] == 1
        assert payload["matches"][0]["id"] == "c_2"
        assert payload["matches"][0]["matched_fields"] == ["turn"]
        assert payload["matches"][0]["snippets"][0]["text"].endswith("[truncated]")

    asyncio.run(run())
    assert calls == [("c_1", 3), ("c_2", 3)]


def test_chat_export_returns_markdown_and_json(monkeypatch):
    import src.tools.manage as manage_tools

    class FakeClient:
        def list_chats(self):
            return [SimpleNamespace(cid="c_export", title="Export me", is_pinned=True, timestamp=1760000000)]

        async def read_chat(self, chat_id, limit=100):
            assert chat_id == "c_export"
            assert limit == 2
            return SimpleNamespace(
                cid=chat_id,
                turns=[
                    SimpleNamespace(role="user", text="hello"),
                    SimpleNamespace(role="model", text="world"),
                ],
            )

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        markdown_result = await mcp.call_tool(
            "gemini_export_chat",
            {"chat_id": "c_export", "limit": 2},
        )
        markdown = _tool_text(markdown_result)
        assert "Gemini Chat Export: Export me" in markdown
        assert "### 1. user" in markdown

        json_result = await mcp.call_tool(
            "gemini_export_chat",
            {"chat_id": "c_export", "limit": 2, "response_format": "json"},
        )
        payload = json.loads(_tool_text(json_result))
        assert payload["metadata"]["title"] == "Export me"
        assert payload["turns"][1]["text"] == "world"

    asyncio.run(run())


def test_web_feature_probe_uses_observed_rpc_shapes_without_raw_response(monkeypatch):
    import src.tools.manage as manage_tools

    calls = []

    class FakeResponse:
        status_code = 200
        text = json.dumps([["wrb.fr", "sJBwce", "[[null]]", None, None, None, "generic"]])

    class FakeClient:
        async def _batch_execute(self, payloads, source_path="/app", close_on_error=True):
            serialized = payloads[0].serialize()
            calls.append((serialized, source_path, close_on_error))
            return FakeResponse()

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)
        result = await mcp.call_tool(
            "gemini_probe_web_features",
            {"surface": "library", "response_format": "json"},
        )
        payload = json.loads(_tool_text(result))
        assert payload["surface"] == "library"
        assert payload["count"] == 3
        assert payload["results"][0]["rpcid"] == "sJBwce"
        assert payload["results"][0]["ok"] is True
        assert "raw_preview" not in _tool_text(result)
        assert "[[null]]" not in _tool_text(result)

    asyncio.run(run())

    first_payload, source_path, close_on_error = calls[0]
    assert first_payload == ["sJBwce", "[[1,2]]", None, "generic"]
    assert source_path == "/app/library"
    assert close_on_error is False


def test_web_capabilities_manifest_describes_observed_pro_surface():
    import src.tools.manage as manage_tools

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        json_result = await mcp.call_tool(
            "gemini_get_web_capabilities",
            {"response_format": "json"},
        )
        payload = json.loads(_tool_text(json_result))
        assert payload["account_tier"] == "Gemini Web Pro"
        assert [item["alias"] for item in payload["models"]] == ["flash-lite", "flash", "pro"]
        assert payload["thinking_levels"][1]["id"] == "extended"
        assert any(item["name"] == "scheduled_actions" for item in payload["settings_menu"])
        assert any(probe["rpcid"] == "K4WWud" for probe in payload["feature_probes"])
        assert any(probe["rpcid"] == "MaZiqc" for probe in payload["feature_probes"])
        assert any(probe["rpcid"] == "XPSWpd" for probe in payload["feature_probes"])
        assert any(probe["rpcid"] == "MyzX6c" for probe in payload["feature_probes"])
        assert "gemini_get_tool_manifest" in payload["mcp_tools"]["account"]
        assert "gemini_get_scheduled_action" in payload["mcp_tools"]["account"]
        assert "gemini_create_scheduled_action" in payload["mcp_tools"]["account"]
        assert "gemini_delete_scheduled_action" in payload["mcp_tools"]["account"]

        markdown_result = await mcp.call_tool("gemini_get_web_capabilities", {})
        text = _tool_text(markdown_result)
        assert "Gemini Web Pro 能力清单" in text
        assert "Canvas" in text

    asyncio.run(run())


def test_tool_manifest_and_annotations_expose_agent_safety_metadata():
    import src.tools.manage as manage_tools

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        tools = await mcp.list_tools()
        by_name = {tool.name: tool for tool in tools}
        assert by_name["gemini_list_chats"].annotations.readOnlyHint is True
        assert by_name["gemini_delete_chat"].annotations.destructiveHint is True
        assert by_name["gemini_manage_gems"].annotations.destructiveHint is True

        json_result = await mcp.call_tool(
            "gemini_get_tool_manifest",
            {"response_format": "json", "scope": "history"},
        )
        payload = json.loads(_tool_text(json_result))
        assert payload["scope"] == "history"
        assert payload["groups"] == {"history": 5}
        delete_entry = next(item for item in payload["tools"] if item["name"] == "gemini_delete_chat")
        assert delete_entry["destructive"] is True
        assert delete_entry["availability"] == ["manage", "all"]
        export_entry = next(item for item in payload["tools"] if item["name"] == "gemini_export_chat")
        assert export_entry["privacy"] == "reads_private_chat_text"
        assert any(item["name"] == "chat_history_find_and_export" for item in payload["workflows"])

        chat_scope_result = await mcp.call_tool(
            "gemini_get_tool_manifest",
            {"response_format": "json", "scope": "chat"},
        )
        chat_payload = json.loads(_tool_text(chat_scope_result))
        assert chat_payload["scope"] == "chat"
        assert chat_payload["groups"] == {"core": 7}
        assert {item["name"] for item in chat_payload["tools"]} >= {
            "gemini_chat",
            "gemini_chat_stream",
            "gemini_start_chat",
            "gemini_send_message",
        }
        assert any(item["name"] == "current_pro_generation" for item in chat_payload["workflows"])

        markdown_result = await mcp.call_tool("gemini_get_tool_manifest", {})
        manifest_text = _tool_text(markdown_result)
        assert "Gemini MCP Tool Manifest" in manifest_text
        assert "privacy:" in manifest_text
        assert "destructive" in manifest_text

    asyncio.run(run())


def test_tool_manifest_marks_currently_enabled_tools(monkeypatch):
    import src.tools.manage as manage_tools

    monkeypatch.delenv("GEMINI_TOOLS", raising=False)
    default_payload = manage_tools._tool_manifest_payload("all")
    default_tools = {item["name"]: item for item in default_payload["tools"]}
    assert default_payload["current_tool_groups"] == ["core"]
    assert default_tools["gemini_chat"]["current_enabled"] is True
    assert default_tools["gemini_get_cookie_status"]["current_enabled"] is True
    assert default_tools["gemini_get_tool_manifest"]["current_enabled"] is True
    assert default_tools["gemini_manage_prompts"]["current_enabled"] is False

    monkeypatch.setenv("GEMINI_TOOLS", "all")
    all_payload = manage_tools._tool_manifest_payload("all")
    all_tools = {item["name"]: item for item in all_payload["tools"]}
    assert all_tools["gemini_get_tool_manifest"]["current_enabled"] is True
    assert all_tools["gemini_manage_prompts"]["current_enabled"] is False
    assert all_payload["current_enabled_count"] == 37

    monkeypatch.setenv("GEMINI_TOOLS", "prompts")
    prompts_payload = manage_tools._tool_manifest_payload("all")
    prompts_tools = {item["name"]: item for item in prompts_payload["tools"]}
    assert prompts_tools["gemini_manage_prompts"]["current_enabled"] is True
    assert prompts_tools["gemini_chat"]["current_enabled"] is False
    assert prompts_tools["gemini_get_cookie_status"]["current_enabled"] is True
    assert prompts_tools["gemini_get_tool_manifest"]["current_enabled"] is True


def test_parsed_web_surface_tools_return_structured_data(monkeypatch):
    import src.tools.manage as manage_tools

    calls = []

    class FakeClient:
        def __init__(self):
            self.deleted_scheduled_ids = set()

        async def _batch_execute(self, payloads, source_path="/app", close_on_error=True):
            serialized = payloads[0].serialize()
            calls.append((serialized, source_path, close_on_error))
            rpcid = serialized[0]
            if rpcid == "K4WWud":
                body = [
                    ["p1", "Shared chat", False, None, "https://gemini.google.com/share/abc"],
                    ["p2", "Second share", True, None, "https://gemini.google.com/share/def"],
                ]
            elif rpcid == "qpEbW":
                body = [[[[None, 11], 2, 3, [1781794431, 0], 100, 80]], ""]
            elif rpcid == "cYRIkd":
                body = [
                    [
                        [["canvas"], "Canvas", "Create and edit documents", ""],
                        [["guided_learning"], "Guided Learning", "Study help", ""],
                    ]
                ]
            elif rpcid == "MaZiqc":
                body = [
                    None,
                    "cursor",
                    [
                        [
                            "task-1",
                            "Morning brief",
                            True,
                            None,
                            None,
                            [1781794431, 0],
                            None,
                            "Daily",
                            None,
                            2,
                        ],
                        [
                            "task-2",
                            "Weekly plan",
                            False,
                            None,
                            None,
                            [1781880831, 0],
                            None,
                            "Weekly",
                            None,
                            2,
                        ]
                    ],
                ]
            elif rpcid == "XPSWpd":
                body = [[
                    [
                        "sched-1",
                        [
                            ["Send morning brief", "Every day at 9 AM", "Morning brief"],
                            None,
                            [[[[9]], None, None, None, [1, 4], None, None, ["Asia/Shanghai"]]],
                            ["c_task", "en", "gem"],
                            None,
                            [True, True],
                        ],
                        [None, None, None, None, [1], [1781794431, 0], [1781794431, 0]],
                    ],
                    [
                        "sched-2",
                        [
                            ["Send weekly plan", "Every Friday at 8 AM", "Weekly plan"],
                            None,
                            [[[[8]], None, None, None, [1, 4], None, None, ["Asia/Shanghai"]]],
                            ["c_task_2", "en", "gem"],
                            None,
                            [False, False],
                        ],
                        [None, None, None, None, [1], [1781880831, 0], [1781880831, 0]],
                    ],
                ]]
            elif rpcid == "Jba3ib":
                body = [
                    [
                        "created-task",
                        [
                            "created-task",
                            [
                                ["Create instructions", "Every day by 8 AM", "Create title"],
                                None,
                                [[[[8]], None, None, None, [1, 4], None, None, ["Asia/Shanghai"]]],
                                [None, "zh-CN"],
                                None,
                                [True],
                            ],
                        ],
                    ]
                ]
            elif rpcid == "kwDCne":
                task_metadata = [None, [1], None, None, None, [1781794431, 0], [1781794431, 0]]
                if "created-task" in self.deleted_scheduled_ids:
                    task_metadata = [None, None, None, [], None, None, [1781794431, 0]]
                body = [[
                    "created-task",
                    [
                        ["Create instructions", "Every day by 8 AM", "Create title"],
                        None,
                        [[[[8]], None, None, None, [1, 4], None, None, ["Asia/Shanghai"]]],
                        [None, "zh-CN"],
                        None,
                        [True],
                    ],
                    task_metadata,
                ]]
            elif rpcid == "Q4Gw3c":
                payload = json.loads(serialized[1])
                for item in payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []:
                    self.deleted_scheduled_ids.add(item)
                body = [[]]
            elif rpcid == "MyzX6c":
                body = [True, [[1, True, 1, 0, None, 1], [35, True, 0, 0, None, 0]]]
            else:
                body = []
            return SimpleNamespace(
                status_code=200,
                text=json.dumps([["wrb.fr", rpcid, json.dumps(body), None, None, None, "generic"]]),
            )

    async def noop_initialize():
        return None

    monkeypatch.setattr(manage_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(manage_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        manage_tools.register_manage_tools(mcp)

        links_result = await mcp.call_tool(
            "gemini_list_public_links",
            {"response_format": "json"},
        )
        links = json.loads(_tool_text(links_result))
        assert links["items"][0]["title"] == "Shared chat"
        assert links["items"][0]["url"].endswith("/abc")
        assert links["total_count"] == 2

        links_page_result = await mcp.call_tool(
            "gemini_list_public_links",
            {"response_format": "json", "limit": 1, "offset": 1},
        )
        links_page = json.loads(_tool_text(links_page_result))
        assert links_page["items"][0]["id"] == "p2"
        assert links_page["has_more"] is False

        usage_result = await mcp.call_tool(
            "gemini_get_usage_limits",
            {"scope": "quota", "response_format": "json"},
        )
        usage = json.loads(_tool_text(usage_result))
        assert usage["results"][0]["entries"][0]["remaining_value"] == 80
        assert usage["results"][0]["entries"][0]["reset_time"].endswith("UTC")

        library_result = await mcp.call_tool(
            "gemini_list_library_capabilities",
            {"response_format": "json"},
        )
        library = json.loads(_tool_text(library_result))
        assert library["items"][0]["name"] == "Canvas"
        assert library["items"][0]["aliases"] == ["canvas"]
        assert library["total_count"] == 2

        library_page_result = await mcp.call_tool(
            "gemini_list_library_capabilities",
            {"response_format": "json", "limit": 1, "offset": 1},
        )
        library_page = json.loads(_tool_text(library_page_result))
        assert library_page["items"][0]["name"] == "Guided Learning"

        scheduled_result = await mcp.call_tool(
            "gemini_list_scheduled_actions",
            {"scope": "active", "response_format": "json", "limit": 1},
        )
        scheduled = json.loads(_tool_text(scheduled_result))
        item = scheduled["results"][0]["items"][0]
        assert item["id"] == "sched-1"
        assert item["title"] == "Morning brief"
        assert item["enabled"] is True
        assert item["schedule_label"] == "Every day at 9 AM"
        assert item["hour"] == 9
        assert item["timezone_name"] == "Asia/Shanghai"
        assert item["created_time"].endswith("UTC")
        assert scheduled["results"][0]["total_count"] == 1
        assert scheduled["results"][0]["diagnostic"]["raw_entry_count"] == 2

        scheduled_page_result = await mcp.call_tool(
            "gemini_list_scheduled_actions",
            {"scope": "inactive", "response_format": "json", "limit": 1},
        )
        scheduled_page = json.loads(_tool_text(scheduled_page_result))
        assert scheduled_page["results"][0]["items"][0]["id"] == "sched-2"

        get_scheduled_result = await mcp.call_tool(
            "gemini_get_scheduled_action",
            {"action_id": "created-task", "response_format": "json"},
        )
        get_scheduled = json.loads(_tool_text(get_scheduled_result))
        assert get_scheduled["ok"] is True
        assert get_scheduled["item"]["id"] == "created-task"
        assert get_scheduled["item"]["title"] == "Create title"
        assert get_scheduled["item"]["task_state"] == "running"
        assert get_scheduled["diagnostic"]["matched_task"] is True

        create_result = await mcp.call_tool(
            "gemini_create_scheduled_action",
            {
                "title": "Create title",
                "instructions": "Create instructions",
                "hour": 8,
                "timezone_name": "Asia/Shanghai",
                "response_format": "json",
            },
        )
        created = json.loads(_tool_text(create_result))
        assert created["ok"] is True
        assert created["id"] == "created-task"
        assert created["title"] == "Create title"
        assert created["schedule_label"] == "Every day by 8 AM"
        assert created["visible_in_registry"] is False
        assert created["readable_by_id_after_create"] is True
        assert created["task_state_after_create"] == "running"
        assert created["task_state_id_after_create"] == 3
        assert created["verification_status"] == "readable_by_id_not_visible_in_registry"

        delete_result = await mcp.call_tool(
            "gemini_delete_scheduled_action",
            {"action_id": "created-task", "response_format": "json"},
        )
        deleted = json.loads(_tool_text(delete_result))
        assert deleted["ok"] is True
        assert deleted["id"] == "created-task"
        assert deleted["readable_by_id_after_delete"] is True
        assert deleted["deleted_by_id_after_delete"] is True
        assert deleted["task_state_after_delete"] == "deleted"
        assert deleted["task_state_id_after_delete"] == 6
        assert deleted["verification_status"] == "deleted_state_by_id"

        modes_result = await mcp.call_tool(
            "gemini_get_tool_mode_status",
            {"response_format": "json"},
        )
        modes = json.loads(_tool_text(modes_result))
        assert modes["leading_enabled"] is True
        assert modes["total_count"] == 2
        assert modes["items"][0]["mode_id"] == 1
        assert modes["items"][1]["state"] == 0

        modes_page_result = await mcp.call_tool(
            "gemini_get_tool_mode_status",
            {"response_format": "json", "limit": 1, "offset": 1},
        )
        modes_page = json.loads(_tool_text(modes_page_result))
        assert modes_page["items"][0]["mode_id"] == 35

    asyncio.run(run())

    create_call = next(call for call in calls if call[0][0] == "Jba3ib")
    create_payload, create_source_path, create_close_on_error = create_call
    assert create_source_path == "/scheduled"
    assert create_close_on_error is False
    assert create_payload[3] == "generic"
    serialized_create_body = json.loads(create_payload[1])
    assert serialized_create_body[0][0][0] == "Create instructions"
    assert serialized_create_body[0][0][2] == "Create title"
    assert serialized_create_body[0][2][0][0][0][0] == 8
    assert serialized_create_body[0][2][0][7] == ["Asia/Shanghai"]

    delete_call = next(call for call in calls if call[0][0] == "Q4Gw3c")
    delete_payload, delete_source_path, delete_close_on_error = delete_call
    assert delete_payload == ["Q4Gw3c", "[null,[\"created-task\"]]", None, "generic"]
    assert delete_source_path == "/scheduled"
    assert delete_close_on_error is False


def test_url_analysis_preserves_url_and_timeout(monkeypatch):
    import src.tools.file as file_tools

    calls = []

    class FakeClient:
        async def generate_content(self, prompt, model=None, thinking_level=None, timeout=None):
            calls.append((prompt, model, timeout))
            return SimpleNamespace(text="ok", images=[])

    async def noop_initialize():
        return None

    monkeypatch.setattr(file_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(file_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        file_tools.register_file_tools(mcp)
        await mcp.call_tool(
            "gemini_analyze_url",
            {
                "url": "https://example.com",
                "analysis_prompt": "Summarize this page in one sentence.",
                "model": "fast",
            },
        )

    asyncio.run(run())

    prompt, model, timeout = calls[0]
    assert "https://example.com" in prompt
    assert "Summarize this page in one sentence." in prompt
    assert timeout == 60


def test_deep_research_uses_library_flag_and_timeout(monkeypatch):
    import src.tools.research as research_tools

    calls = []

    class FakeClient:
        async def generate_content(
            self, prompt, model=None, deep_research=None, thinking_level=None, timeout=None
        ):
            calls.append((prompt, model, deep_research, timeout))
            return SimpleNamespace(text="report", sources=[])

    async def noop_initialize():
        return None

    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(research_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        research_tools.register_research_tools(mcp)
        result = await mcp.call_tool(
            "gemini_deep_research",
            {
                "query": "What is MCP?",
                "model": "thinking",
                "timeout_seconds": 45,
            },
        )
        assert "report" in _tool_text(result)

    asyncio.run(run())

    prompt, model, deep_research, timeout = calls[0]
    assert prompt.startswith("What is MCP?")
    assert "Requested MCP model alias: thinking" in prompt
    assert deep_research is True
    assert timeout == 45


def test_deep_research_runs_full_library_workflow(monkeypatch):
    import src.tools.research as research_tools

    calls = []

    class FakePlan:
        research_id = "research-1"
        title = "MCP"
        response_text = "plan"

    class FakeStatus:
        state = "complete"
        done = True
        notes = ["finished"]

    class FakeResult:
        plan = FakePlan()
        start_output = None
        final_output = SimpleNamespace(text="final report")
        statuses = [FakeStatus()]
        done = True

    class FakeClient:
        def start_chat(self, model=None, metadata=None, cid=None):
            calls.append(("start_chat", model))
            return SimpleNamespace()

        async def create_deep_research_plan(self, query, chat=None, model=None):
            calls.append(("create_plan", query, model))
            return FakePlan()

        async def start_deep_research(self, plan, chat=None):
            calls.append(("start_research", plan.research_id))
            return SimpleNamespace(text="started")

        async def wait_for_deep_research(self, plan, poll_interval=None, timeout=None):
            calls.append(("wait", plan.research_id, poll_interval, timeout))
            return FakeResult()

    async def noop_initialize():
        return None

    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(research_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        research_tools.register_research_tools(mcp)
        result = await mcp.call_tool(
            "gemini_deep_research",
            {
                "query": "What is MCP?",
                "model": "thinking",
                "timeout_seconds": 45,
                "poll_interval_seconds": 4,
            },
        )
        text = _tool_text(result)
        assert "完成: 是" in text
        assert "final report" in text

    asyncio.run(run())

    assert calls[0][0] == "start_chat"
    assert getattr(calls[0][1], "model_name", None) == "unspecified"
    assert calls[1][0] == "create_plan"
    assert calls[1][1].startswith("What is MCP?")
    assert "Requested MCP model alias: thinking" in calls[1][1]
    assert getattr(calls[1][2], "model_name", None) == "unspecified"
    assert calls[2:] == [
        ("start_research", "research-1"),
        ("wait", "research-1", 4, 45),
    ]


def test_deep_research_falls_back_to_chat_polling_without_research_id(monkeypatch):
    import src.tools.research as research_tools

    class FakePlan:
        research_id = None
        title = "MCP"
        response_text = "plan"
        cid = "c_1"

    class FakeClient:
        def __init__(self):
            self.polls = 0

        def start_chat(self, model=None, metadata=None, cid=None):
            return SimpleNamespace(cid="c_1")

        async def create_deep_research_plan(self, query, chat=None, model=None):
            return FakePlan()

        async def start_deep_research(self, plan, chat=None):
            return SimpleNamespace(text="Great, I'm on it. I'll let you know when the research is finished.")

        async def wait_for_deep_research(self, *args, **kwargs):
            raise AssertionError("research_id-less plans should use chat polling")

        async def fetch_latest_chat_response(self, cid):
            self.polls += 1
            if self.polls == 1:
                return SimpleNamespace(text="Great, I'm on it. I'll let you know when the research is finished.")
            return SimpleNamespace(text="final report")

    fake_client = FakeClient()

    async def noop_initialize():
        return None

    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: fake_client)
    monkeypatch.setattr(research_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        research_tools.register_research_tools(mcp)
        result = await mcp.call_tool(
            "gemini_deep_research",
            {
                "query": "What is MCP?",
                "model": "thinking",
                "timeout_seconds": 10,
                "poll_interval_seconds": 3,
            },
        )
        text = _tool_text(result)
        assert "完成: 是" in text
        assert "final report" in text

    asyncio.run(run())


def test_deep_research_timeout_does_not_present_start_message_as_report(monkeypatch):
    import src.tools.research as research_tools

    start_message = "Great. While I'm researching, feel free to leave this chat."

    class FakePlan:
        research_id = None
        title = "MCP"
        response_text = "plan"
        cid = "c_1"

    class FakeClient:
        def start_chat(self, model=None, metadata=None, cid=None):
            return SimpleNamespace(cid="c_1")

        async def create_deep_research_plan(self, query, chat=None, model=None):
            return FakePlan()

        async def start_deep_research(self, plan, chat=None):
            return SimpleNamespace(text=start_message)

        async def wait_for_deep_research(self, *args, **kwargs):
            raise AssertionError("research_id-less plans should use chat polling")

        async def fetch_latest_chat_response(self, cid):
            return SimpleNamespace(text=start_message)

    async def noop_initialize():
        return None

    monkeypatch.setattr(research_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(research_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        research_tools.register_research_tools(mcp)
        result = await mcp.call_tool(
            "gemini_deep_research",
            {
                "query": "What is MCP?",
                "model": "thinking",
                "timeout_seconds": 3,
                "poll_interval_seconds": 3,
            },
        )
        text = _tool_text(result)
        assert "完成: 否" in text
        assert start_message not in text
        assert "研究已启动" in text

    asyncio.run(run())


def test_media_tool_returns_clear_upstream_failure(monkeypatch):
    import src.tools.media as media_tools

    class FakeClient:
        async def generate_content(self, prompt, files=None, model=None, thinking_level=None, timeout=None):
            raise RuntimeError("The original request may have been silently aborted by Google.")

    async def noop_initialize():
        return None

    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(media_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        media_tools.register_media_tools(mcp)
        result = await mcp.call_tool(
            "gemini_generate_media",
            {
                "prompt": "tiny video",
                "media_type": "video",
                "model": "fast",
                "timeout_seconds": 3,
            },
        )
        text = _tool_text(result)
        assert "video 生成失败" in text
        assert "通用 generate_content" in text

    asyncio.run(run())


def test_media_tool_reports_empty_media_response(monkeypatch):
    import src.tools.media as media_tools

    class FakeClient:
        async def generate_content(self, prompt, files=None, model=None, thinking_level=None, timeout=None):
            return SimpleNamespace(text="", images=[], videos=[], media=[])

    async def noop_initialize():
        return None

    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(media_tools, "initialize_client", noop_initialize)

    async def run():
        mcp = FastMCP("test")
        media_tools.register_media_tools(mcp)
        result = await mcp.call_tool(
            "gemini_generate_music",
            {
                "prompt": "short jingle",
                "model": "flash-lite",
                "timeout_seconds": 3,
            },
        )
        text = _tool_text(result)
        assert "后端: Lyria 3" in text
        assert "没有返回文本、图片、视频或音乐资源" in text

    asyncio.run(run())


def test_media_tool_saves_generated_music_files(monkeypatch, tmp_path):
    import src.tools.media as media_tools

    class FakeMedia:
        title = "theme"
        mp3_url = "https://example.com/theme.mp3"
        url = ""

        async def save(self, **kwargs):
            assert kwargs["path"] == str(tmp_path)
            assert kwargs["filename"] == "theme"
            assert kwargs["download_type"] == "both"
            output = tmp_path / "theme.mp3"
            output.write_bytes(b"fake mp3")
            return {"audio": str(output)}

    class FakeClient:
        async def generate_content(self, prompt, files=None, model=None, thinking_level=None, timeout=None):
            return SimpleNamespace(text="done", images=[], videos=[], media=[FakeMedia()])

    async def noop_initialize():
        return None

    async def noop_cleanup(client=None):
        return 0

    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(media_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(media_tools, "cleanup_due_remote_chats", noop_cleanup)
    monkeypatch.setattr(media_tools, "schedule_remote_chat_cleanup_from_response", lambda *args, **kwargs: None)
    monkeypatch.setattr(media_tools, "_probe_duration", lambda path: 91.25)

    async def run():
        mcp = FastMCP("test")
        media_tools.register_media_tools(mcp)
        result = await mcp.call_tool(
            "gemini_generate_music",
            {
                "prompt": "theme",
                "model": "flash",
                "thinking_level": "extended",
                "output_dir": str(tmp_path),
                "filename": "theme",
            },
        )
        text = _tool_text(result)
        assert "Saved files:" in text
        assert "audio:" in text
        assert "91.25s" in text

    asyncio.run(run())


def test_media_tool_recovers_music_urls_from_raw_chat_card():
    import orjson
    import src.tools.media as media_tools

    mp3_url = "https://contribution.usercontent.google.com/download?filename=song.mp3"
    mp4_url = "https://contribution.usercontent.google.com/download?filename=song.mp4"
    candidate = [
        "rc_1",
        ["done\nhttp://googleusercontent.com/generated_music_content/0"],
    ]
    while len(candidate) <= 12:
        candidate.append(None)
    candidate[12] = [
        {
            "87": [
                [None, [None, 4, "song.mp3", None, None, None, None, ["thumb", mp3_url]]],
                [None, [None, 4, "song.mp4", None, None, None, None, ["thumb", mp4_url]]],
            ]
        }
    ]
    conv_turn = [[None, "r_1"], None, None, [[candidate]]]
    body = [[conv_turn]]
    raw = [[None, None, orjson.dumps(body).decode("utf-8")]]

    class FakeResponse:
        text = orjson.dumps(raw).decode("utf-8")

    class FakeClient:
        proxy = None

        async def _batch_execute(self, calls):
            return FakeResponse()

    async def run():
        media = await media_tools._fetch_music_media_from_chat(FakeClient(), "c_1")
        assert len(media) == 2
        assert media[0].mp3_url == mp3_url
        assert media[0].title == "song.mp3"
        assert media[1].url == mp4_url
        assert media[1].title == "song.mp4"

    asyncio.run(run())


def test_media_tool_routes_music_and_image_to_current_web_backends(monkeypatch):
    import src.tools.media as media_tools

    calls = []

    class FakeClient:
        async def generate_content(self, prompt, files=None, model=None, thinking_level=None, timeout=None):
            calls.append((prompt, model, thinking_level))
            return SimpleNamespace(text="ok", images=[], videos=[], media=[])

    async def noop_initialize():
        return None

    async def noop_cleanup(client=None):
        return 0

    monkeypatch.setattr(media_tools, "get_gemini_client", lambda: FakeClient())
    monkeypatch.setattr(media_tools, "initialize_client", noop_initialize)
    monkeypatch.setattr(media_tools, "cleanup_due_remote_chats", noop_cleanup)
    monkeypatch.setattr(media_tools, "schedule_remote_chat_cleanup_from_response", lambda *args, **kwargs: None)

    async def run():
        mcp = FastMCP("test")
        media_tools.register_media_tools(mcp)

        image_result = await mcp.call_tool(
            "gemini_generate_media",
            {"prompt": "studio portrait", "media_type": "image", "model": "pro"},
        )
        music_result = await mcp.call_tool(
            "gemini_generate_music",
            {"prompt": "cinematic trailer", "model": "pro"},
        )

        assert "后端: Nano Banana 2" in _tool_text(image_result)
        assert "Pro redo" in _tool_text(image_result)
        assert "后端: Lyria 3 Pro" in _tool_text(music_result)

    asyncio.run(run())

    assert calls == [
        ("Generate an image. Prompt: studio portrait", "gemini-3-flash", "standard"),
        ("Create music/audio using Gemini's music generation capability. Prompt: cinematic trailer", "gemini-3-pro", "extended"),
    ]


def test_client_wrapper_applies_extra_cookies(monkeypatch):
    import src.client_wrapper as client_wrapper
    import src.thinking_client as thinking_client

    applied = {}

    class FakeGeminiClient:
        def __init__(self, psid, psidts, proxy=None):
            self.psid = psid
            self.psidts = psidts
            self.proxy = proxy
            self._cookies = {}

        @property
        def cookies(self):
            return self._cookies

        @cookies.setter
        def cookies(self, value):
            applied.update(value)
            self._cookies.update(value)

    fake_cookie_manager = SimpleNamespace(
        get_cookie=lambda: SimpleNamespace(
            extra_cookies={
                "__Secure-1PSID": "psid",
                "__Secure-1PSIDTS": "psidts",
                "__Secure-1PSIDCC": "psidcc",
                "__Secure-3PSID": "three-psid",
            }
        )
    )

    monkeypatch.setattr(thinking_client, "ThinkingLevelGeminiClient", FakeGeminiClient)
    monkeypatch.setattr("src.client_manager.COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr("src.client_manager.get_cookie_manager", lambda: fake_cookie_manager)
    monkeypatch.setattr(client_wrapper._client_manager, "_client", None)
    monkeypatch.setattr(client_wrapper._client_manager, "_initialized", False)
    monkeypatch.setenv("GEMINI_PSID", "psid")
    monkeypatch.setenv("GEMINI_PSIDTS", "psidts")

    client = client_wrapper.get_gemini_client()

    assert isinstance(client, FakeGeminiClient)
    assert applied["__Secure-1PSIDCC"] == "psidcc"
    assert applied["__Secure-3PSID"] == "three-psid"


def test_client_wrapper_isolates_browser_cookie_cache(monkeypatch, tmp_path):
    import src.client_wrapper as client_wrapper
    import src.thinking_client as thinking_client

    class FakeGeminiClient:
        def __init__(self, psid, psidts, proxy=None):
            self._cookies = {}

        @property
        def cookies(self):
            return self._cookies

        @cookies.setter
        def cookies(self, value):
            self._cookies.update(value)

    fake_cookie_manager = SimpleNamespace(
        get_cookie=lambda: SimpleNamespace(
            source="browser_chrome",
            extra_cookies={
                "__Secure-1PSID": "psid",
                "__Secure-1PSIDTS": "psidts",
            },
        )
    )
    cache_dir = tmp_path / "gemini_web_mcp_webapi_cookie_cache"
    cache_dir.mkdir()
    stale_cache = cache_dir / ".cached_cookies_stale.json"
    stale_cache.write_text("[]")

    monkeypatch.setattr(thinking_client, "ThinkingLevelGeminiClient", FakeGeminiClient)
    monkeypatch.setattr("src.client_manager.COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr("src.client_manager.get_cookie_manager", lambda: fake_cookie_manager)
    monkeypatch.setattr("src.client_manager.tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(client_wrapper._client_manager, "_client", None)
    monkeypatch.setattr(client_wrapper._client_manager, "_initialized", False)
    monkeypatch.delenv("GEMINI_COOKIE_PATH", raising=False)
    monkeypatch.setenv("GEMINI_PSID", "psid")
    monkeypatch.setenv("GEMINI_PSIDTS", "psidts")

    client_wrapper.get_gemini_client()

    assert os.environ["GEMINI_COOKIE_PATH"] == str(cache_dir)
    assert not stale_cache.exists()


def test_cookie_manager_includes_chrome_auto_candidate(monkeypatch, tmp_path):
    import src.cookie_manager as cookie_manager

    class FakeCookie:
        def __init__(self, name, value, domain=".google.com"):
            self.name = name
            self.value = value
            self.domain = domain

    class FakeBrowserCookie3:
        @staticmethod
        def chrome(cookie_file=None, domain_name=""):
            if cookie_file is None:
                return [FakeCookie("__Secure-1PSID", "auto")]
            if "Default" in cookie_file:
                return [FakeCookie("__Secure-1PSID", "default")]
            return [FakeCookie("__Secure-1PSID", "profile")]

    chrome_root = tmp_path / "Library/Application Support/Google/Chrome"
    (chrome_root / "Default").mkdir(parents=True)
    (chrome_root / "Default/Cookies").write_text("")
    (chrome_root / "Profile 1").mkdir()
    (chrome_root / "Profile 1/Cookies").write_text("")

    monkeypatch.setattr(cookie_manager.sys, "platform", "darwin")
    monkeypatch.setattr(cookie_manager.Path, "home", lambda: tmp_path)

    candidates = cookie_manager.CookieManager._browser_cookie_candidates(
        FakeBrowserCookie3,
        "chrome",
        FakeBrowserCookie3.chrome,
        {"__Secure-1PSID"},
    )

    assert [name for name, _ in candidates] == ["auto", "Default", "Profile 1"]


def test_cookie_manager_lists_and_selects_explicit_chrome_profile(monkeypatch, tmp_path):
    import src.cookie_manager as cookie_manager

    class FakeCookie:
        def __init__(self, name, value, domain=".google.com"):
            self.name = name
            self.value = value
            self.domain = domain

    class FakeBrowserCookie3:
        @staticmethod
        def chrome(cookie_file=None, domain_name=""):
            if cookie_file is None:
                return [FakeCookie("__Secure-1PSID", "auto-secret"), FakeCookie("__Secure-1PSIDTS", "auto-ts")]
            if "Default" in cookie_file:
                return []
            return [FakeCookie("__Secure-1PSID", "profile-secret"), FakeCookie("SID", "profile-sid")]

        firefox = chrome
        edge = chrome
        opera = chrome
        brave = chrome

    chrome_root = tmp_path / "Library/Application Support/Google/Chrome"
    (chrome_root / "Default").mkdir(parents=True)
    (chrome_root / "Default/Cookies").write_text("")
    (chrome_root / "Profile 1").mkdir()
    (chrome_root / "Profile 1/Cookies").write_text("")
    (chrome_root / "Local State").write_text(json.dumps({"profile": {"last_used": "Default"}}))

    monkeypatch.setitem(sys.modules, "browser_cookie3", FakeBrowserCookie3)
    monkeypatch.setattr(cookie_manager.sys, "platform", "darwin")
    monkeypatch.setattr(cookie_manager.Path, "home", lambda: tmp_path)

    profiles = cookie_manager.CookieManager.list_browser_cookie_profiles("chrome", validate=False)

    assert [item["profile"] for item in profiles] == ["auto", "Default", "Profile 1"]
    assert profiles[0]["has_psid"] is True
    assert profiles[0]["has_psidts"] is True
    assert profiles[1]["has_psid"] is False
    assert profiles[1]["chrome_selected_profile"] is True
    assert profiles[2]["chrome_selected_profile"] is False
    assert profiles[2]["chrome_selected_profile_directory"] == "Default"
    assert "auto-secret" not in json.dumps(profiles)

    selected = cookie_manager.CookieManager.get_cookies_from_browser("chrome", profile="Profile 1")

    assert selected["__Secure-1PSID"] == "profile-secret"
    assert selected["SID"] == "profile-sid"


def test_client_wrapper_ignores_stale_local_proxy(monkeypatch):
    import src.client_wrapper as client_wrapper

    monkeypatch.setenv("GEMINI_PROXY", "http://127.0.0.1:1")

    assert client_wrapper.get_configured_proxy() is None


def test_remote_chat_cleanup_deletes_expired_chat(monkeypatch):
    import src.client_wrapper as client_wrapper

    deleted = []

    class FakeClient:
        async def delete_chat(self, cid):
            deleted.append(cid)

    async def run():
        client_wrapper._cleanup_manager._pending_cleanup.clear()
        monkeypatch.setattr(client_wrapper._client_manager, "_client", FakeClient())
        monkeypatch.setattr(client_wrapper._client_manager, "_initialized", True)
        monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "0")

        client_wrapper.schedule_remote_chat_cleanup("c_test_cleanup")
        await asyncio.sleep(0.01)

        assert deleted == ["c_test_cleanup"]
        assert "c_test_cleanup" not in client_wrapper.list_pending_remote_chat_cleanup()

    asyncio.run(run())


def test_remote_chat_cleanup_respects_retain_chat(monkeypatch):
    import src.client_wrapper as client_wrapper

    async def run():
        client_wrapper._cleanup_manager._pending_cleanup.clear()
        monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "0")

        client_wrapper.schedule_remote_chat_cleanup("c_keep", retain_chat=True)
        await asyncio.sleep(0.01)

        assert client_wrapper.list_pending_remote_chat_cleanup() == {}

    asyncio.run(run())
