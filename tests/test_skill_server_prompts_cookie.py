"""skill_server 的 prompts 和 cookie 工具测试。

这两个 skill 工具此前零功能测试（仅 annotation 断言）。本文件覆盖：
- prompts: 4 个 action + invalid action + 缺参数早退
- cookie: 3 个 action + invalid action

skill_server 的工具在模块级用 @mcp.tool 注册到全局 mcp，但函数本身是
可 await 的 async 函数，直接调用即可，无需走 mcp.call_tool。
"""

import asyncio
from unittest.mock import patch

import src.skill_server as skill_server


# ---------------------------------------------------------------------------
# prompts 工具
# ---------------------------------------------------------------------------


class _FakePromptManager:
    """模拟 skill_server.PromptManager 的最小接口。"""

    def __init__(self):
        self._data = {}

    def list_all(self):
        return sorted(self._data.values(), key=lambda x: x.get("name", "").lower())

    def get_by_name(self, name):
        for p in self._data.values():
            if p.get("name", "").lower() == name.lower():
                return p
        return None

    def create(self, name, content, category="general"):
        prompt_id = name.lower().replace(" ", "_")
        self._data[prompt_id] = {
            "id": prompt_id,
            "name": name,
            "content": content,
            "category": category,
        }
        return prompt_id

    def delete(self, name):
        prompt = self.get_by_name(name)
        if prompt:
            del self._data[prompt["id"]]
            return True
        return False


def test_skill_server_prompts_lifecycle_and_dispatch():
    """覆盖 prompts 的 4 个 action + invalid action + 缺参数早退。"""
    fake_mgr = _FakePromptManager()
    with patch.object(skill_server, "get_prompts", return_value=fake_mgr):
        async def run():
            # 空列表
            r = await skill_server.prompts(action="list")
            assert r[0].text == "No prompts"

            # create 缺 name/content
            r = await skill_server.prompts(action="create")
            assert r[0].text == "Name and content required"

            # create 成功
            r = await skill_server.prompts(
                action="create", name="Test Prompt", content="hello"
            )
            assert r[0].text == "Created: Test Prompt"

            # list 非空
            r = await skill_server.prompts(action="list")
            assert "1. Test Prompt" in r[0].text

            # get 缺 name
            r = await skill_server.prompts(action="get")
            assert r[0].text == "Name required"

            # get 找到
            r = await skill_server.prompts(action="get", name="test prompt")
            text = r[0].text
            assert "Test Prompt" in text
            assert "hello" in text

            # get 找不到
            r = await skill_server.prompts(action="get", name="missing")
            assert r[0].text == "Not found"

            # delete 缺 name
            r = await skill_server.prompts(action="delete")
            assert r[0].text == "Name required"

            # delete 成功
            r = await skill_server.prompts(action="delete", name="Test Prompt")
            assert r[0].text == "Deleted: Test Prompt"

            # delete 再删返回 Not found
            r = await skill_server.prompts(action="delete", name="Test Prompt")
            assert r[0].text == "Not found"

            # invalid action
            r = await skill_server.prompts(action="bogus")
            assert r[0].text == "Invalid action"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# cookie 工具
# ---------------------------------------------------------------------------


def test_skill_server_cookie_status_when_cookie_present():
    with patch.object(skill_server, "get_cookie_status", return_value={"has_cookie": True}):
        async def run():
            r = await skill_server.cookie(action="status")
            assert r[0].text == "Cookie: OK"

        asyncio.run(run())


def test_skill_server_cookie_status_when_cookie_missing():
    with patch.object(skill_server, "get_cookie_status", return_value={"has_cookie": False}):
        async def run():
            r = await skill_server.cookie(action="status")
            assert r[0].text == "Cookie: Missing"

        asyncio.run(run())


def test_skill_server_cookie_get_success():
    with patch.object(skill_server, "get_cookie_from_browser", return_value=True):
        async def run():
            r = await skill_server.cookie(action="get")
            assert r[0].text == "Cookie: Loaded"

        asyncio.run(run())


def test_skill_server_cookie_get_failure_with_profile():
    with patch.object(skill_server, "get_cookie_from_browser", return_value=False):
        async def run():
            r = await skill_server.cookie(action="get", profile="Profile 1")
            assert r[0].text == "Cookie Profile 1: Failed"

        asyncio.run(run())


def test_skill_server_cookie_profiles_lists_entries():
    fake_profiles = [
        {
            "profile": "Default",
            "has_psid": True,
            "chrome_selected_profile": "Default",
            "account_available": True,
            "scheduled_registry_count": 3,
        },
        {
            "profile": "Profile 1",
            "has_psid": False,
            "chrome_selected_profile": "",
            "account_available": None,
            "scheduled_registry_count": "unvalidated",
        },
        {"profile": "Broken", "error": "permission denied"},
    ]
    with patch.object(skill_server, "list_browser_cookie_profiles", return_value=fake_profiles):
        async def run():
            r = await skill_server.cookie(action="profiles")
            text = r[0].text
            assert "Default: psid=yes" in text
            assert "chrome_selected=yes" in text
            assert "account_available=yes" in text
            assert "Profile 1: psid=no" in text
            assert "error: permission denied" in text

        asyncio.run(run())


def test_skill_server_cookie_profiles_empty_returns_no_profiles():
    with patch.object(skill_server, "list_browser_cookie_profiles", return_value=[]):
        async def run():
            r = await skill_server.cookie(action="profiles")
            assert r[0].text == "No profiles"

        asyncio.run(run())


def test_skill_server_cookie_invalid_action():
    async def run():
        r = await skill_server.cookie(action="bogus")
        assert r[0].text == "Invalid action"

    asyncio.run(run())
