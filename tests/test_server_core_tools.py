"""src/server.py 的核心工具行为测试。

调研发现 server.py 覆盖率 83%（90 stmts, 15 miss），缺失行集中在 3 个工具
函数体（此前仅有注解形状测试，零行为覆盖）：

- `gemini_get_tool_manifest`（lines 97-100）：scope 透传 + json/markdown 双格式
- `gemini_reset`（lines 106-107）：调 reset_client + 返回固定文本
- `gemini_doctor`（lines 117-120）：browser/validate_browser 透传 + json/markdown 双格式

剩余 6 miss（lines 198-201, 205）为 `main()` / `__main__` 阻塞入口，不可测试。

测试策略：通过 `server.mcp.call_tool(name, kwargs)` 经 MCP 分发调用（与
test_server_cookie_tools.py 同模式），monkeypatch 底层 payload/format 函数
隔离真实依赖。
"""

import asyncio
import json

import src.server as server


async def _call_tool(name, **kwargs):
    """通过 server.mcp.call_tool 调用工具，返回 TextContent 列表。"""
    content, _structured = await server.mcp.call_tool(name, kwargs)
    return content


# ---------------------------------------------------------------------------
# gemini_get_tool_manifest
# ---------------------------------------------------------------------------


def test_get_tool_manifest_markdown_default(monkeypatch):
    """默认 response_format=markdown → 调 _format_tool_manifest_markdown（lines 97-100）。"""
    captured = {}

    def fake_payload(scope):
        captured["scope"] = scope
        return {"tools": [{"name": "gemini_chat"}]}

    monkeypatch.setattr(server, "_tool_manifest_payload", fake_payload)
    monkeypatch.setattr(
        server, "_format_tool_manifest_markdown",
        lambda payload: f"MARKDOWN:{payload['tools'][0]['name']}",
    )

    result = asyncio.run(_call_tool("gemini_get_tool_manifest", scope="core"))
    assert len(result) == 1
    assert result[0].text == "MARKDOWN:gemini_chat"
    assert captured["scope"] == "core"


def test_get_tool_manifest_json_format(monkeypatch):
    """response_format=json → json.dumps(payload)（line 98-99 json 分支）。"""
    payload = {"tools": [{"name": "gemini_chat"}]}
    monkeypatch.setattr(server, "_tool_manifest_payload", lambda scope: payload)

    result = asyncio.run(_call_tool("gemini_get_tool_manifest", scope="all", response_format="json"))
    assert len(result) == 1
    assert json.loads(result[0].text) == payload


def test_get_tool_manifest_passes_scope(monkeypatch):
    """scope 参数透传到 _tool_manifest_payload。"""
    captured = {}
    monkeypatch.setattr(server, "_tool_manifest_payload", lambda scope: captured.setdefault("scope", scope))
    monkeypatch.setattr(server, "_format_tool_manifest_markdown", lambda payload: "")

    asyncio.run(_call_tool("gemini_get_tool_manifest", scope="chat"))
    assert captured["scope"] == "chat"


# ---------------------------------------------------------------------------
# gemini_reset
# ---------------------------------------------------------------------------


def test_gemini_reset_calls_reset_client(monkeypatch):
    """gemini_reset 调 reset_client 并返回固定文本（lines 106-107）。"""
    called = {"n": 0}
    monkeypatch.setattr(server, "reset_client", lambda: called.__setitem__("n", called["n"] + 1))

    result = asyncio.run(_call_tool("gemini_reset"))
    assert len(result) == 1
    assert "客户端已重置" in result[0].text
    assert called["n"] == 1


# ---------------------------------------------------------------------------
# gemini_doctor
# ---------------------------------------------------------------------------


def test_gemini_doctor_markdown_default(monkeypatch):
    """默认 response_format=markdown → 调 _format_doctor_markdown（lines 117-120）。"""
    captured = {}

    def fake_payload(browser, validate_browser):
        captured["browser"] = browser
        captured["validate_browser"] = validate_browser
        return {"overall_status": "ok"}

    monkeypatch.setattr(server, "_doctor_payload", fake_payload)
    monkeypatch.setattr(server, "_format_doctor_markdown", lambda payload: f"DOCTOR:{payload['overall_status']}")

    result = asyncio.run(_call_tool("gemini_doctor"))
    assert len(result) == 1
    assert result[0].text == "DOCTOR:ok"
    assert captured["browser"] == "chrome"
    assert captured["validate_browser"] is False


def test_gemini_doctor_json_format(monkeypatch):
    """response_format=json → json.dumps(payload)（line 118-119 json 分支）。"""
    payload = {"overall_status": "warn", "checks": []}
    monkeypatch.setattr(server, "_doctor_payload", lambda browser, validate_browser: payload)

    result = asyncio.run(_call_tool("gemini_doctor", browser="firefox", validate_browser=True, response_format="json"))
    assert len(result) == 1
    assert json.loads(result[0].text) == payload


def test_gemini_doctor_passes_browser_and_validate(monkeypatch):
    """browser 与 validate_browser 参数透传到 _doctor_payload。"""
    captured = {}
    monkeypatch.setattr(
        server, "_doctor_payload",
        lambda browser, validate_browser: captured.update(browser=browser, validate_browser=validate_browser) or {},
    )
    monkeypatch.setattr(server, "_format_doctor_markdown", lambda payload: "")

    asyncio.run(_call_tool("gemini_doctor", browser="edge", validate_browser=True))
    assert captured["browser"] == "edge"
    assert captured["validate_browser"] is True
