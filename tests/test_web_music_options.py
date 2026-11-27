"""Native music options retain their meaning across the MCP creation surfaces."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp import Client

from src import skill_server
from src.adapters import MCPServer
from src.domain import DomainResult, LongOperationData, OperationState
from src.onboarding import domain_result_from_call
from src.services import background
from src.surfaces import function
from src.tools import media
from src.tools.annotations import MUTATES_REMOTE


_HANDLE = "op_" + "a" * 32
_PROMPT = "An original piano melody."
_MUSIC_OPTIONS = {"music_duration": "short", "music_vocals": "instrumental"}
_ENUMS = {"music_duration": ["short", "standard"], "music_vocals": ["instrumental", "vocals", "auto"]}


@pytest.fixture
def primary():
    server = MCPServer("music options")
    media.register_media_tools(server)
    return server


@pytest.fixture
def submit(monkeypatch):
    accepted = DomainResult.success(LongOperationData(
        operation="music", state=OperationState.ACCEPTED, operation_id=_HANDLE,
    ))
    submit = AsyncMock(return_value=accepted)
    monkeypatch.setattr(background, "submit_media", submit)
    monkeypatch.setattr(function, "submit_media", submit)
    return submit


def test_music_options_are_optional_and_forwarded_by_every_registered_surface(primary, submit):
    async def run():
        async with Client(function.mcp, mode="auto", cache=None) as client:
            lean = {item.name: item for item in (await client.list_tools()).tools}["generate-music"]
            assert lean.annotations == MUTATES_REMOTE
            for field, internal in (("duration", "music_duration"), ("vocals", "music_vocals")):
                assert field not in lean.input_schema.get("required", ())
                assert {"enum": _ENUMS[internal], "type": "string"} in lean.input_schema["properties"][field]["anyOf"]
            for lean_opts, expected in (
                ({"duration": "short", "vocals": "instrumental"}, _MUSIC_OPTIONS),
                ({"vocals": "auto"}, {"music_vocals": "auto"}),
                ({}, {}),
            ):
                response = await client.call_tool("generate-music", {"prompt": _PROMPT, "wait_seconds": 0, **lean_opts})
                result = domain_result_from_call(response)
                assert result["ok"] and result["data"]["operation_id"] == _HANDLE
                request = submit.await_args.args[0]
                assert request["media_type"] == "music"
                assert {field: request[field] for field in _ENUMS if field in request} == expected

        surfaces = (
            (function.build_server(full=True), "gemini_generate_music", {"wait_seconds": 0}),
            (primary, "gemini_generate_media", {"media_type": "music"}),
            (primary, "gemini_generate_music", {}),
            (skill_server.mcp, "create", {"type": "music"}),
        )
        for server, tool, kind in surfaces:
            async with Client(server, mode="auto", cache=None) as client:
                registered = {item.name: item for item in (await client.list_tools()).tools}[tool]
                assert registered.annotations == MUTATES_REMOTE
                for field, choices in _ENUMS.items():
                    schema = registered.input_schema
                    assert field not in schema.get("required", ())
                    assert schema["properties"][field]["default"] is None
                    assert {"enum": choices, "type": "string"} in schema["properties"][field]["anyOf"]
                for options in (_MUSIC_OPTIONS, {"music_vocals": "auto"}, {}):
                    response = await client.call_tool(tool, {"prompt": _PROMPT, **kind, **options})
                    result = domain_result_from_call(response)
                    assert result["ok"] and result["data"]["operation_id"] == _HANDLE
                    request = submit.await_args.args[0]
                    assert request["media_type"] == "music"
                    assert {field: request[field] for field in _ENUMS if field in request} == options

    asyncio.run(run())
    assert submit.await_count == 15


def test_music_options_on_another_media_kind_fail_before_dispatch(primary, submit):
    async def run():
        surfaces = (
            (primary, "gemini_generate_media", "media_type"),
            (skill_server.mcp, "create", "type"),
        )
        for server, tool, kind in surfaces:
            async with Client(server, mode="auto", cache=None) as client:
                for option, value in _MUSIC_OPTIONS.items():
                    outcome = await client.call_tool(tool, {"prompt": _PROMPT, kind: "video", option: value})
                    result = domain_result_from_call(outcome)
                    assert not result["ok"] and result["error"]["code"] == "INVALID_ARGUMENT"

    asyncio.run(run())
    submit.assert_not_awaited()


def test_synchronous_music_forwards_only_explicit_options(monkeypatch, primary):
    generate = AsyncMock(side_effect=TimeoutError("synthetic generation stopped"))
    for adapter in (media, skill_server):
        monkeypatch.setattr(adapter, "get_gemini_client", SimpleNamespace)
        monkeypatch.setattr(adapter, "initialize_client", AsyncMock())
        monkeypatch.setattr(adapter, "cleanup_due_remote_chats", AsyncMock())
        monkeypatch.setattr(adapter, "generate_media_response", generate)

    async def run():
        surfaces = (
            (primary, "gemini_generate_media", {"media_type": "music"}),
            (primary, "gemini_generate_music", {}),
            (skill_server.mcp, "create", {"type": "music"}),
        )
        for server, tool, kind in surfaces:
            for options in (_MUSIC_OPTIONS, {}):
                result = await server.call_tool(tool, {"prompt": _PROMPT, "background": False, **kind, **options})
                assert not domain_result_from_call(result)["ok"]
                dispatched = generate.await_args.kwargs
                assert dispatched["media_type"] == "music"
                assert {field: dispatched[field] for field in _ENUMS if field in dispatched} == options

    asyncio.run(run())
    assert generate.await_count == 6
