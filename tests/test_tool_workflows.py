import asyncio
import sys
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP


def _tool_text(result):
    content, _meta = result
    return content[0].text


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
        assert "gemini_upload_file" in names
        assert "gemini_manage_prompts" not in names

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
        assert "gemini_manage_prompts" not in names
        assert "gemini_list_chats" not in names

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


def test_current_web_models_resolve_thinking_mode_buckets():
    from src.constants import (
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
    assert resolve_media_request("flash-lite", "music")["request_model"] == "gemini-3-flash"
    assert resolve_media_request("pro", "music")["backend_label"] == "Lyria 3 Pro"


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
            return [SimpleNamespace(cid="c_latest", title="Latest chat")]

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

    asyncio.run(run())


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
    assert prompt == "What is MCP?"
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

    assert calls == [
        ("start_chat", "gemini-3-flash-thinking"),
        ("create_plan", "What is MCP?", "gemini-3-flash-thinking"),
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
            {"prompt": "cinematic trailer", "model": "flash-lite"},
        )

        assert "后端: Nano Banana 2" in _tool_text(image_result)
        assert "Pro redo" in _tool_text(image_result)
        assert "后端: Lyria 3" in _tool_text(music_result)

    asyncio.run(run())

    assert calls == [
        ("Generate an image. Prompt: studio portrait", "gemini-3-flash", "standard"),
        ("Create music/audio using Gemini's music generation capability. Prompt: cinematic trailer", "gemini-3-flash", "extended"),
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
    monkeypatch.setattr(client_wrapper, "COOKIE_MANAGER_AVAILABLE", True)
    monkeypatch.setattr(client_wrapper, "get_cookie_manager", lambda: fake_cookie_manager)
    monkeypatch.setattr(client_wrapper, "_client", None)
    monkeypatch.setattr(client_wrapper, "_initialized", False)
    monkeypatch.setenv("GEMINI_PSID", "psid")
    monkeypatch.setenv("GEMINI_PSIDTS", "psidts")

    client = client_wrapper.get_gemini_client()

    assert isinstance(client, FakeGeminiClient)
    assert applied["__Secure-1PSIDCC"] == "psidcc"
    assert applied["__Secure-3PSID"] == "three-psid"


def test_remote_chat_cleanup_deletes_expired_chat(monkeypatch):
    import src.client_wrapper as client_wrapper

    deleted = []

    class FakeClient:
        async def delete_chat(self, cid):
            deleted.append(cid)

    async def run():
        client_wrapper._pending_remote_chat_cleanup.clear()
        monkeypatch.setattr(client_wrapper, "_client", FakeClient())
        monkeypatch.setattr(client_wrapper, "_initialized", True)
        monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "0")

        client_wrapper.schedule_remote_chat_cleanup("c_test_cleanup")
        await asyncio.sleep(0.01)

        assert deleted == ["c_test_cleanup"]
        assert "c_test_cleanup" not in client_wrapper.list_pending_remote_chat_cleanup()

    asyncio.run(run())


def test_remote_chat_cleanup_respects_retain_chat(monkeypatch):
    import src.client_wrapper as client_wrapper

    async def run():
        client_wrapper._pending_remote_chat_cleanup.clear()
        monkeypatch.setenv("GEMINI_CHAT_RETENTION_SECONDS", "0")

        client_wrapper.schedule_remote_chat_cleanup("c_keep", retain_chat=True)
        await asyncio.sleep(0.01)

        assert client_wrapper.list_pending_remote_chat_cleanup() == {}

    asyncio.run(run())
