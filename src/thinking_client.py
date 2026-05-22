"""Gemini Web thinking-level transport compatibility."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, AsyncGenerator, Iterator

import orjson
from gemini_webapi import GeminiClient
from gemini_webapi.constants import Endpoint
from gemini_webapi.types import ModelOutput

from .constants import resolve_thinking_level_id, resolve_thinking_mode_id


_thinking_request: ContextVar[tuple[int, int] | None] = ContextVar(
    "gemini_thinking_request",
    default=None,
)


def inject_thinking_level(
    request_data: dict[str, Any],
    *,
    mode_id: int,
    level_id: int,
) -> dict[str, Any]:
    """Add the Web UI thinking-level fields to a StreamGenerate request."""
    f_req = request_data.get("f.req")
    if not isinstance(f_req, str):
        return request_data

    outer_request = orjson.loads(f_req)
    if not isinstance(outer_request, list) or len(outer_request) < 2:
        return request_data

    inner_payload = outer_request[1]
    if not isinstance(inner_payload, str):
        return request_data

    inner_request = orjson.loads(inner_payload)
    if not isinstance(inner_request, list):
        return request_data

    if len(inner_request) <= 80:
        inner_request.extend([None] * (81 - len(inner_request)))
    inner_request[79] = mode_id
    inner_request[80] = level_id

    patched = dict(request_data)
    outer_request[1] = orjson.dumps(inner_request).decode("utf-8")
    patched["f.req"] = orjson.dumps(outer_request).decode("utf-8")
    return patched


class ThinkingLevelGeminiClient(GeminiClient):
    """Gemini client that carries the current Web UI thinking-level selector."""

    async def init(self, *args: Any, **kwargs: Any) -> None:
        await super().init(*args, **kwargs)
        self._install_thinking_transport()

    async def generate_content(
        self,
        *args: Any,
        model: Any = None,
        thinking_level: str | None = None,
        **kwargs: Any,
    ) -> ModelOutput:
        token = self._set_thinking_request(model, thinking_level)
        try:
            if model is None:
                return await super().generate_content(*args, **kwargs)
            return await super().generate_content(*args, model=model, **kwargs)
        finally:
            _thinking_request.reset(token)

    async def generate_content_stream(
        self,
        *args: Any,
        model: Any = None,
        thinking_level: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ModelOutput, None]:
        token = self._set_thinking_request(model, thinking_level)
        try:
            if model is None:
                stream = super().generate_content_stream(*args, **kwargs)
            else:
                stream = super().generate_content_stream(*args, model=model, **kwargs)
            async for output in stream:
                yield output
        finally:
            _thinking_request.reset(token)

    def _set_thinking_request(self, model: Any, thinking_level: str | None):
        if thinking_level is None and _thinking_request.get():
            return _thinking_request.set(_thinking_request.get())

        level_id = resolve_thinking_level_id(thinking_level)
        if thinking_level is not None and level_id is None:
            raise ValueError("thinking_level 仅支持 standard/extended（或 标准/扩展）。")

        mode_id = resolve_thinking_mode_id(model)
        return _thinking_request.set((mode_id, level_id) if mode_id and level_id else None)

    @contextmanager
    def thinking_scope(self, model: Any, thinking_level: str) -> Iterator[None]:
        """Keep a thinking level active through upstream helper workflows."""
        token = self._set_thinking_request(model, thinking_level)
        try:
            yield
        finally:
            _thinking_request.reset(token)

    def _install_thinking_transport(self) -> None:
        session = self.client
        if not session or getattr(session, "_mcp_thinking_stream", False):
            return

        stream = session.stream

        def stream_with_thinking(method: str, url: str, *args: Any, **kwargs: Any):
            request = _thinking_request.get()
            data = kwargs.get("data")
            if request and url == Endpoint.GENERATE and isinstance(data, dict):
                mode_id, level_id = request
                kwargs["data"] = inject_thinking_level(
                    data,
                    mode_id=mode_id,
                    level_id=level_id,
                )
                headers = dict(kwargs.get("headers") or {})
                headers["x-goog-ext-73010990-jspb"] = "[0,0,0]"
                kwargs["headers"] = headers
            return stream(method, url, *args, **kwargs)

        session.stream = stream_with_thinking
        session._mcp_thinking_stream = True
