"""The account surface exposes explicit account management only."""

import asyncio

from mcp import Client

from src.surfaces.account import mcp


EXPECTED = {
    "gemini_history", "gemini_delete_chat", "gemini_notebooks", "gemini_move_chat_to_notebook",
    "gemini_account_inventory", "gemini_list_scheduled_actions", "gemini_get_scheduled_action",
    "gemini_create_scheduled_action", "gemini_delete_scheduled_action", "gemini_manage_gems", "gemini_cleanup",
    "gemini_manage_chat", "gemini_tasks", "gemini_task_browser", "gemini_preferences",
    "gemini_skills", "gemini_manage_notebook",
}


def test_account_catalog_and_safety_annotations():
    async def run():
        async with Client(mcp, mode="auto", cache=None) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}
    tools = asyncio.run(run())
    assert set(tools) == EXPECTED
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["gemini_history"].annotations.read_only_hint is True
    assert tools["gemini_notebooks"].annotations.read_only_hint is True
    assert tools["gemini_delete_chat"].annotations.destructive_hint is True
    assert tools["gemini_delete_scheduled_action"].annotations.destructive_hint is True
    assert tools["gemini_cleanup"].annotations.destructive_hint is True
    assert tools["gemini_tasks"].annotations.destructive_hint is True
    assert tools['gemini_task_browser'].annotations.destructive_hint is True
    assert tools['gemini_preferences'].annotations.read_only_hint is False
    assert tools['gemini_preferences'].annotations.idempotent_hint is False
    assert tools['gemini_preferences'].annotations.open_world_hint is True
    for name in ('gemini_skills', 'gemini_manage_notebook'):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is True
        assert tools[name].annotations.idempotent_hint is False
        assert tools[name].annotations.open_world_hint is True
    assert {'progress', 'create_schedule', 'update_schedule', 'pause_schedule', 'resume_schedule', 'delete_schedule'} <= set(
        tools['gemini_tasks'].input_schema['properties']['action']['enum'])
    assert tools["gemini_notebooks"].input_schema["properties"]["source"]["default"] == "notebooklm"
    control = tools["gemini_manage_chat"].annotations
    assert control.read_only_hint is False and control.destructive_hint is False
    assert control.idempotent_hint is True and control.open_world_hint is True
    history_schema = tools["gemini_history"].input_schema["properties"]
    assert history_schema["response_format"]["default"] == "compact"
    assert history_schema["max_chars_per_turn"]["default"] == 800
    assert not {"gemini_cleanup_test_artifacts", "gemini_doctor", "gemini_get_cookie_status"} & set(tools)


def test_account_catalog_over_real_stdio(tmp_path):
    import os
    import sys
    from pathlib import Path
    from mcp import StdioServerParameters, stdio_client

    environment = os.environ.copy()
    for name in ("GEMINI_PSID", "GEMINI_PSIDTS", "GEMINI_PSIDCC"):
        environment.pop(name, None)
    environment.update(GEMINI_BROWSER_AUTO_LOAD="false", GEMINI_AUTO_REFRESH="false",
                       GEMINI_OPERATION_DB_PATH=str(tmp_path / "operations.sqlite"),
                       GEMINI_CLEANUP_DB_PATH=str(tmp_path / "cleanup.sqlite"))
    params = StdioServerParameters(command=sys.executable,
                                   args=["-c", "from src.surfaces.account import mcp; mcp.run()"],
                                   env=environment, cwd=Path(__file__).resolve().parents[1])
    async def run():
        async with Client(stdio_client(params), mode="auto", cache=None) as client:
            return {tool.name for tool in (await client.list_tools()).tools}
    assert asyncio.run(run()) == EXPECTED
