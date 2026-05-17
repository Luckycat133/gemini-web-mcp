import asyncio
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
