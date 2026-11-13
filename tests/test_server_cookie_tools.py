"""src/server.py 的 cookie 工具行为测试。

调研发现 server.py 的 6 个工具中，gemini_get_cookie_status /
gemini_list_browser_cookie_profiles / gemini_get_cookie_from_browser 三个
cookie 相关工具仅有注解形状测试（test_server_utility_tools_have_annotations），
零行为覆盖。本文件补充：

- gemini_get_cookie_status: Manager 不可用 / 可用+已设置 / 可用+未设置+需刷新
- gemini_list_browser_cookie_profiles:
  * 空 profiles
  * 含 error 的 profile
  * 正常 profile 多字段渲染（psid/psidts/cookies/selected/selected_dir/account/available/scheduled_count）
  * response_format=json
  * list_browser_cookie_profiles 抛异常 → handle_error 兜底
- gemini_get_cookie_from_browser:
  * success=True 无 profile
  * success=True 带 profile
  * success=False
  * 抛异常 → handle_error 兜底
"""

import asyncio
import json
import os
import sys

os.environ["GEMINI_TOOLS"] = "all"

if "src.server" in sys.modules:
    del sys.modules["src.server"]
import src.server as server

# Cookie 工具 handlers 定义在 src.tools.infrastructure，并从该模块的全局命名空间
# 调用 get_cookie_status / list_browser_cookie_profiles / get_cookie_from_browser，
# 故需 monkeypatch 该模块（而非 src.server）才能生效。
async def _call_tool(name, **kwargs):
    """通过 server.mcp.call_tool 调用工具，返回 TextContent 列表。"""
    return (await server.mcp.call_tool(name, kwargs)).content


# ---------------------------------------------------------------------------
# gemini_get_cookie_status
# ---------------------------------------------------------------------------


def test_get_cookie_status_unavailable(monkeypatch):
    """Cookie Manager 不可用 → 返回警告文本。"""
    monkeypatch.setattr(server, "get_cookie_status", lambda: {"available": False})

    async def run():
        return await _call_tool("gemini_get_cookie_status")

    result = asyncio.run(run())
    assert len(result) == 1
    assert "Cookie Manager 不可用" in result[0].text


def test_get_cookie_status_available_with_cookie(monkeypatch):
    """可用 + has_cookie=True + needs_refresh=False → 已设置 + 无需刷新。"""
    monkeypatch.setattr(server, "get_cookie_status", lambda: {
        "available": True,
        "status": "VALID",
        "has_cookie": True,
        "needs_refresh": False,
        "source": "env",
    })

    async def run():
        return await _call_tool("gemini_get_cookie_status")

    result = asyncio.run(run())
    text = result[0].text
    assert "VALID" in text
    assert "已设置" in text
    assert "无需刷新" in text
    assert "env" in text


def test_get_cookie_status_reports_autoload_denial_without_claiming_refresh(monkeypatch):
    monkeypatch.setattr(server, "get_cookie_status", lambda: {
        "available": True,
        "status": "unknown",
        "has_cookie": False,
        "browser_auto_load": True,
        "browser_auth_reason": "BROWSER_COOKIE_ACCESS_DENIED",
    })

    async def run():
        return await _call_tool("gemini_get_cookie_status")

    result = asyncio.run(run())
    text = result[0].text
    assert "未设置" in text
    assert "暂无可刷新凭据" in text
    assert "浏览器自动读取: 开启" in text and "BROWSER_COOKIE_ACCESS_DENIED" in text


# ---------------------------------------------------------------------------
# gemini_list_browser_cookie_profiles
# ---------------------------------------------------------------------------


def test_list_browser_cookie_profiles_empty(monkeypatch):
    """空 profiles → 'No profiles found'。"""
    monkeypatch.setattr(server, "list_browser_cookie_profiles", lambda b, validate=True: [])

    async def run():
        return await _call_tool("gemini_list_browser_cookie_profiles", browser="chrome")

    result = asyncio.run(run())
    text = result[0].text
    assert "chrome Cookie Profiles" in text
    assert "No profiles found" in text


def test_list_browser_cookie_profiles_with_error_entry(monkeypatch):
    """含 error 的 profile → 渲染 'error: ...' 并 continue。"""
    monkeypatch.setattr(server, "list_browser_cookie_profiles", lambda b, validate=True: [
        {"error": "Cookie Manager unavailable", "error_code": "BROWSER_COOKIE_ACCESS_TIMEOUT"},
    ])

    async def run():
        return await _call_tool("gemini_list_browser_cookie_profiles", browser="chrome")

    result = asyncio.run(run())
    assert "error: Cookie Manager unavailable" in result[0].text
    assert "BROWSER_COOKIE_ACCESS_TIMEOUT" in result[0].text


def test_list_browser_cookie_profiles_normal_entry_renders_all_fields(monkeypatch):
    """正常 profile → 多字段渲染。"""
    monkeypatch.setattr(server, "list_browser_cookie_profiles", lambda b, validate=True: [
        {
            "profile": "Default",
            "has_psid": True,
            "has_psidts": False,
            "cookie_count": 12,
            "chrome_selected_profile": True,
            "chrome_selected_profile_directory": "/Users/x/Library/Application Support/Google/Chrome/Default",
            "account_status": "valid",
            "account_available": True,
            "scheduled_registry_count": 3,
        },
    ])

    async def run():
        return await _call_tool("gemini_list_browser_cookie_profiles", browser="chrome")

    text = asyncio.run(run())[0].text
    assert "Default:" in text
    assert "psid=yes" in text
    assert "psidts=no" in text
    assert "cookies=12" in text
    assert "chrome_selected=yes" in text
    assert "selected_dir=/Users/x/Library/Application Support/Google/Chrome/Default" in text
    assert "account=valid" in text
    assert "available=yes" in text
    assert "scheduled_registry_count=3" in text


def test_list_browser_cookie_profiles_account_available_unknown(monkeypatch):
    """account_available=None → available=unknown。"""
    monkeypatch.setattr(server, "list_browser_cookie_profiles", lambda b, validate=True: [
        {
            "profile": "Profile 1",
            "has_psid": False,
            "has_psidts": False,
            "cookie_count": 0,
            "chrome_selected_profile": False,
            "chrome_selected_profile_directory": None,
            "account_status": "unvalidated",
            "account_available": None,
            "scheduled_registry_count": "unvalidated",
        },
    ])

    async def run():
        return await _call_tool("gemini_list_browser_cookie_profiles", browser="chrome")

    text = asyncio.run(run())[0].text
    assert "available=unknown" in text
    assert "selected_dir=unknown" in text


def test_list_browser_cookie_profiles_response_format_json(monkeypatch):
    """response_format=json → 返回可解析 JSON。"""
    monkeypatch.setattr(server, "list_browser_cookie_profiles", lambda b, validate=True: [
        {"profile": "Default", "has_psid": True, "error": None},
    ])

    async def run():
        return await _call_tool(
            "gemini_list_browser_cookie_profiles",
            browser="chrome", response_format="json",
        )

    result = asyncio.run(run())
    data = json.loads(result[0].text)
    assert "profiles" in data
    assert data["profiles"][0]["profile"] == "Default"


def test_list_browser_cookie_profiles_handles_exception(monkeypatch):
    """list_browser_cookie_profiles 抛异常 → handle_error 兜底返回错误响应。"""
    def raise_error(browser, validate=True):
        raise RuntimeError("synthetic-private-body")

    monkeypatch.setattr(server, "list_browser_cookie_profiles", raise_error)

    async def run():
        return await _call_tool("gemini_list_browser_cookie_profiles", browser="firefox")

    result = asyncio.run(run())
    # handle_error + format_error_response 返回错误文本，不抛异常给调用方
    assert len(result) == 1
    assert "错误:" in result[0].text and "请求失败" in result[0].text
    assert "synthetic-private-body" not in result[0].text


# ---------------------------------------------------------------------------
# gemini_get_cookie_from_browser
# ---------------------------------------------------------------------------


def test_get_cookie_from_browser_success_without_profile(monkeypatch):
    """success=True 无 profile → '✅ 已从 {browser} 获取 Cookie'。"""
    monkeypatch.setattr(server, "get_cookie_from_browser", lambda b, profile="": True)

    async def run():
        return await _call_tool("gemini_get_cookie_from_browser", browser="chrome")

    result = asyncio.run(run())
    assert "✅" in result[0].text
    assert "chrome" in result[0].text
    assert "profile=" not in result[0].text


def test_get_cookie_from_browser_success_with_profile(monkeypatch):
    """success=True 带 profile → 含 'profile=...'。"""
    monkeypatch.setattr(server, "get_cookie_from_browser", lambda b, profile="": True)

    async def run():
        return await _call_tool(
            "gemini_get_cookie_from_browser", browser="chrome", profile="Work",
        )

    result = asyncio.run(run())
    assert "profile=Work" in result[0].text


def test_get_cookie_from_browser_failure(monkeypatch):
    """success=False → '❌ 获取失败...'。"""
    monkeypatch.setattr(server, "get_cookie_from_browser", lambda b, profile="": False)

    async def run():
        return await _call_tool("gemini_get_cookie_from_browser", browser="chrome")

    result = asyncio.run(run())
    assert "❌" in result[0].text
    assert "获取失败" in result[0].text
    assert "Gemini 网页版" in result[0].text


def test_get_cookie_from_browser_handles_exception(monkeypatch):
    """get_cookie_from_browser 抛异常 → handle_error 兜底，不向调用方抛异常。

    handle_error 会把 RuntimeError 映射成通用 cookie 错误响应（含"错误"/"解决方案"），
    这里只验证兜底行为本身：返回 1 个 TextContent，且不是成功路径。
    """
    def raise_error(browser, profile=""):
        raise RuntimeError("cookie store locked")

    monkeypatch.setattr(server, "get_cookie_from_browser", raise_error)

    async def run():
        return await _call_tool("gemini_get_cookie_from_browser", browser="chrome")

    result = asyncio.run(run())
    assert len(result) == 1
    text = result[0].text
    # handle_error 返回的错误响应含"错误"或"解决方案"，且不是成功路径
    assert "错误" in text or "解决方案" in text
    assert "已从 chrome 获取 Cookie" not in text
