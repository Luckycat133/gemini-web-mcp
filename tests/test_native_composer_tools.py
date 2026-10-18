"""Current Canvas/Guided Learning selectors exercise the real transport seam."""

import asyncio

import pytest
from gemini_webapi import GeminiClient

from src.infrastructure.web_request_contracts import NativeToolRequestShapeError
from src.thinking_client import ThinkingLevelGeminiClient, WebRequestOptions, _web_request, inject_web_request_options
from tests.test_thinking_client import _make_request_data, _new_client, _parse_inner


@pytest.mark.parametrize("mode,selector", [("canvas", 2), ("guided_learning", 24)])
@pytest.mark.parametrize("streaming", [False, True])
def test_current_composer_tool_reaches_transport_without_companion_fields(monkeypatch, mode, selector, streaming):
    captured = []

    async def generate(_self, *args, **kwargs):
        captured.append((args, kwargs, _web_request.get()))
        return "output"

    async def stream(_self, *args, **kwargs):
        captured.append((args, kwargs, _web_request.get()))
        yield "output"

    monkeypatch.setattr(GeminiClient, "generate_content", generate)
    monkeypatch.setattr(GeminiClient, "generate_content_stream", stream)
    client = _new_client()
    token = _web_request.set(None)
    try:
        async def run():
            arguments = {"tool_mode": mode, "thinking_level": None}
            if streaming:
                return [item async for item in client.generate_content_stream("fixture", **arguments)]
            return await client.generate_content("fixture", **arguments)

        asyncio.run(run())
        args, kwargs, options = captured[0]
        assert args == ("fixture",)
        assert kwargs["current_retry"] == 0
        assert "tool_mode" not in kwargs
        assert options.tool_mode_id == selector
        inner = _parse_inner(inject_web_request_options(_make_request_data(), options))
        assert inner[49] == selector
        assert inner[54:56] == [None, None]
        assert _web_request.get() is None
    finally:
        _web_request.reset(token)


@pytest.mark.parametrize("alias", ["guided_learning", "guided", "guided-learning"])
def test_guided_learning_alias_selects_native_tool_and_preserves_prompt(alias):
    client = _new_client()
    token = client._set_web_request(None, None, alias)
    try:
        options = _web_request.get()
        assert options.tool_mode_id == 24
        assert options.learning_mode_id is None
        assert client._with_learning_prompt(("fixture",), {}, alias) == (("fixture",), {})
    finally:
        _web_request.reset(token)


@pytest.mark.parametrize("kwargs", [
    {"media_mode": "image", "tool_mode": "canvas"},
    {"task_mode": True, "tool_mode": "canvas"},
    {"learning_mode": "quiz", "tool_mode": "canvas"},
    {"learning_mode": "guided_learning", "media_mode": "music"},
])
def test_composer_tools_reject_conflicting_request_modes(kwargs):
    client = _new_client()
    token = _web_request.set(None)
    try:
        with pytest.raises(ValueError):
            client._set_web_request(None, None, kwargs.pop("learning_mode", None), **kwargs)
        assert _web_request.get() is None
    finally:
        _web_request.reset(token)


@pytest.mark.parametrize("data", [{}, {"f.req": "invalid"}, _make_request_data([None] * 69)])
def test_changed_composer_request_fails_before_fallback_chat(data):
    with pytest.raises(NativeToolRequestShapeError):
        inject_web_request_options(data, WebRequestOptions(tool_mode_id=2))


def test_composer_conflict_does_not_overwrite_existing_feature():
    data = _make_request_data()
    inner = _parse_inner(data)
    inner[49] = 14
    with pytest.raises(NativeToolRequestShapeError):
        inject_web_request_options(_make_request_data(inner), WebRequestOptions(tool_mode_id=2))


def test_unknown_native_tool_is_rejected():
    client = object.__new__(ThinkingLevelGeminiClient)
    with pytest.raises(ValueError, match="tool_mode"):
        client._set_web_request(None, None, None, tool_mode="unknown")
