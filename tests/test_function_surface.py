"""The functional surface stays small and delegates to shared workflows."""

import asyncio
from unittest.mock import AsyncMock

from mcp import Client

from src.domain import DomainResult, LongOperationData, OperationState
from src.onboarding import domain_result_from_call
from src.surfaces import function


EXPECTED = {
    "gemini_ask", "gemini_understand_image", "gemini_understand", "gemini_research",
    "gemini_start_chat", "gemini_send_message", "gemini_list_sessions", "gemini_reset_session",
    "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
    "gemini_get_operation_status", "gemini_get_operation_result",
    "gemini_list_research_report_actions", "gemini_create_from_research_report",
    "gemini_run_task",
}


def test_function_catalog_and_safety_annotations():
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return {tool.name: tool for tool in (await client.list_tools()).tools}
    tools = asyncio.run(run())
    assert set(tools) == EXPECTED
    ask_properties = tools["gemini_ask"].input_schema["properties"]
    assert ask_properties["model"]["default"] == "flash"
    assert not {"web_search", "enable_tools", "tools_enabled", "enable_apps"} & ask_properties.keys()
    assert all(tool.annotations is not None for tool in tools.values())
    assert tools["gemini_get_operation_status"].annotations.read_only_hint is True
    assert tools["gemini_get_operation_result"].annotations.destructive_hint is True
    assert tools["gemini_research"].annotations.destructive_hint is True
    assert tools["gemini_run_task"].annotations.destructive_hint is True
    assert tools["gemini_run_task"].input_schema["properties"]["model"]["default"] == "flash"
    assert tools["gemini_generate_video"].input_schema["properties"]["model"]["default"] == "flash"
    assert tools["gemini_generate_music"].input_schema["properties"]["thinking_level"]["default"] == "standard"
    assert tools["gemini_research"].input_schema["properties"]["thinking_level"]["default"] == "standard"
    assert all(tools[name].annotations.read_only_hint is False for name in (
        "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
    ))


def test_creation_wrappers_submit_only_background_jobs(monkeypatch, tmp_path):
    handle = "op_" + "a" * 32
    submit = AsyncMock(return_value=DomainResult.success(
        LongOperationData(operation="test", state=OperationState.ACCEPTED, operation_id=handle),
        operation_state=OperationState.ACCEPTED,
    ))
    monkeypatch.setattr(function, "submit_media", submit)
    image = tmp_path / "input.png"
    image.write_bytes(b"fixture")
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            results = []
            for name, arguments in (
                ("gemini_generate_image", {"prompt": "image"}),
                ("gemini_edit_image", {"prompt": "edit", "image_path": str(image)}),
                ("gemini_generate_video", {"prompt": "video"}),
                ("gemini_generate_video", {"prompt": "video", "model": "argon"}),
                ("gemini_generate_music", {"prompt": "music"}),
                ("gemini_generate_music", {"prompt": "music", "thinking_level": "extended"}),
            ):
                results.append(await client.call_tool(name, arguments))
            return results
    responses = asyncio.run(run())
    assert submit.await_count == 6
    assert [call.args[0]["media_type"] for call in submit.await_args_list] == ["image", "image", "video", "video", "music", "music"]
    assert [call.args[0]["edit"] for call in submit.await_args_list] == [False, True, False, False, False, False]
    assert [submit.await_args_list[i].args[0]["model"] for i in (2, 3)] == ["flash", "argon"]
    assert [submit.await_args_list[i].args[0]["thinking_level"] for i in (4, 5)] == ["standard", "extended"]
    assert all(handle in result.content[0].text for result in responses)
    assert all(domain_result_from_call(result)["ok"] is True for result in responses)


def test_invalid_creation_input_does_not_dispatch(monkeypatch):
    submit = AsyncMock()
    monkeypatch.setattr(function, "submit_media", submit)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            return await client.call_tool("gemini_generate_image", {"prompt": " ", "filename": "../bad"})
    result = asyncio.run(run())
    assert domain_result_from_call(result)["ok"] is False
    submit.assert_not_awaited()


def test_function_catalog_over_real_stdio(tmp_path):
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
                                   args=["-c", "from src.surfaces.function import mcp; mcp.run()"],
                                   env=environment, cwd=Path(__file__).resolve().parents[1])
    async def run():
        async with Client(stdio_client(params), mode="auto", cache=None) as client:
            return {tool.name for tool in (await client.list_tools()).tools}
    assert asyncio.run(run()) == EXPECTED


def test_function_notebook_question_dispatches_background_chat_without_task(monkeypatch):
    from src.services import background
    submit = AsyncMock(return_value=DomainResult.success(
        LongOperationData(operation="gemini_notebook_chat", state=OperationState.ACCEPTED,
                          operation_id="op_" + "a" * 32, execution_mode="chat")))
    monkeypatch.setattr(background, "submit_notebook_chat", submit)
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            assert tools["gemini_ask"].input_schema["properties"]["timeout_seconds"]["default"] == 600
            return await client.call_tool("gemini_ask", {"prompt": "read sources", "notebook_ids": ["notebooks/id"]})
    data = domain_result_from_call(asyncio.run(run()))
    assert data["ok"] and data["data"]["execution_mode"] == "chat"
    submit.assert_awaited_once()
    assert submit.await_args.args[0]["notebook_ids"] == ["notebooks/id"]
    assert submit.await_args.args[0]["timeout_seconds"] == 600
