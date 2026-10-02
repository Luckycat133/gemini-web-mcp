"""Gemini Web thinking-level transport compatibility."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Iterator

import orjson
from gemini_webapi import GeminiClient
from gemini_webapi.constants import Endpoint
from gemini_webapi.types import ModelOutput

from .constants import (
    resolve_learning_mode_config,
    resolve_thinking_level_id,
    resolve_thinking_mode_id,
    supported_learning_modes,
)


@dataclass(frozen=True)
class WebRequestOptions:
    """Gemini Web fields that are not exposed by gemini-webapi yet."""

    thinking_mode_id: int | None = None
    thinking_level_id: int | None = None
    learning_mode_id: int | None = None
    learning_x9b_field: str | None = None
    learning_x9b_value: int | None = None


_web_request: ContextVar[WebRequestOptions | None] = ContextVar(
    "gemini_web_request",
    default=None,
)

_request_timeout: ContextVar[tuple[object, float] | None] = ContextVar("gemini_request_timeout", default=None)
_timeout_initializing_task: ContextVar[tuple[int, int] | None] = ContextVar("gemini_timeout_initializing_task", default=None)


def _is_scoped_timeout_initialization(client: object) -> bool:
    owner = _timeout_initializing_task.get()
    if owner is None or owner[0] != id(client):
        return False
    try:
        return owner[1] == id(asyncio.current_task())
    except RuntimeError:
        return False


@contextmanager
def client_request_timeout(client: object, timeout_seconds: float) -> Iterator[None]:
    """Scope upstream watchdog/recovery settings to one request and its tasks."""
    token = _request_timeout.set((client, timeout_seconds))
    try:
        yield
    finally:
        _request_timeout.reset(token)


def _encode_learning_x9b(field_name: str, value: int) -> list[Any]:
    """Encode the frontend X9b companion request field in JSPB array form."""
    if field_name == "zUa":
        return [[[[[value]]]]]
    if field_name == "QLd":
        return [[[None, [value]]]]
    if field_name == "LYd":
        return [[[None, None, [value]]]]
    if field_name == "h5d":
        return [[[None, None, None, [value]]]]
    raise ValueError(f"unsupported Gemini learning transport field: {field_name}")


def _encode_learning_goa(mode_id: int) -> list[Any]:
    """Encode GOa.H4 selected companion ids in JSPB array form."""
    return [[mode_id]]


def inject_thinking_level(
    request_data: dict[str, Any],
    *,
    mode_id: int,
    level_id: int,
) -> dict[str, Any]:
    """Add the Web UI thinking-level fields to a StreamGenerate request."""
    return inject_web_request_options(
        request_data,
        WebRequestOptions(thinking_mode_id=mode_id, thinking_level_id=level_id),
    )


def inject_web_request_options(
    request_data: dict[str, Any],
    options: WebRequestOptions,
) -> dict[str, Any]:
    """Add Web UI-only fields to a StreamGenerate request."""
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

    required_length = 81
    if options.learning_mode_id:
        required_length = max(required_length, 56)
    if len(inner_request) < required_length:
        inner_request.extend([None] * (required_length - len(inner_request)))

    if options.learning_mode_id:
        if not options.learning_x9b_field or options.learning_x9b_value is None:
            raise ValueError("learning mode transport metadata is incomplete")
        inner_request[54] = _encode_learning_x9b(
            options.learning_x9b_field,
            options.learning_x9b_value,
        )
        inner_request[55] = _encode_learning_goa(options.learning_mode_id)

    if options.thinking_mode_id and options.thinking_level_id:
        inner_request[79] = options.thinking_mode_id
        inner_request[80] = options.thinking_level_id

    patched = dict(request_data)
    outer_request[1] = orjson.dumps(inner_request).decode("utf-8")
    patched["f.req"] = orjson.dumps(outer_request).decode("utf-8")
    return patched


class ThinkingLevelGeminiClient(GeminiClient):
    """Gemini client that carries the current Web UI thinking-level selector."""

    @property
    def timeout(self) -> float:
        default = float(GeminiClient.timeout.__get__(self, type(self)))
        scope = _request_timeout.get()
        return max(default, scope[1]) if scope is not None and scope[0] is self else default

    @timeout.setter
    def timeout(self, value: float) -> None:
        if not _is_scoped_timeout_initialization(self):
            GeminiClient.timeout.__set__(self, value)

    @property
    def watchdog_timeout(self) -> float:
        default = float(GeminiClient.watchdog_timeout.__get__(self, type(self)))
        scope = _request_timeout.get()
        if scope is None or scope[0] is not self:
            return default
        return min(max(default, 120.0), max(scope[1], 120.0))

    @watchdog_timeout.setter
    def watchdog_timeout(self, value: float) -> None:
        if not _is_scoped_timeout_initialization(self):
            GeminiClient.watchdog_timeout.__set__(self, value)

    async def init(self, *args: Any, **kwargs: Any) -> None:
        scope = _request_timeout.get()
        if scope is not None and scope[0] is self:
            # Upstream reconnect forwards its timeout getters into init(). Keep
            # request overrides out of the singleton defaults and background
            # refresh tasks created by that initialization.
            default_timeout = GeminiClient.timeout.__get__(self, type(self))
            default_watchdog = GeminiClient.watchdog_timeout.__get__(self, type(self))
            init_args = list(args)
            if init_args:
                init_args[0] = default_timeout
            else:
                kwargs["timeout"] = default_timeout
            if len(init_args) > 5:
                init_args[5] = default_watchdog
            else:
                kwargs["watchdog_timeout"] = default_watchdog
            timeout_token = _request_timeout.set(None)
            initializing_token = _timeout_initializing_task.set((id(self), id(asyncio.current_task())))
            try:
                await super().init(*init_args, **kwargs)
            finally:
                _timeout_initializing_task.reset(initializing_token)
                _request_timeout.reset(timeout_token)
        else:
            await super().init(*args, **kwargs)
        self._install_thinking_transport()

    async def generate_content(
        self,
        *args: Any,
        model: Any = None,
        thinking_level: str | None = None,
        learning_mode: str | None = None,
        **kwargs: Any,
    ) -> ModelOutput:
        token = self._set_web_request(model, thinking_level, learning_mode)
        try:
            args, kwargs = self._with_learning_prompt(args, kwargs, learning_mode)
            if model is None:
                return await super().generate_content(*args, **kwargs)
            return await super().generate_content(*args, model=model, **kwargs)
        finally:
            _web_request.reset(token)

    async def generate_content_stream(
        self,
        *args: Any,
        model: Any = None,
        thinking_level: str | None = None,
        learning_mode: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ModelOutput, None]:
        token = self._set_web_request(model, thinking_level, learning_mode)
        try:
            args, kwargs = self._with_learning_prompt(args, kwargs, learning_mode)
            if model is None:
                stream = super().generate_content_stream(*args, **kwargs)
            else:
                stream = super().generate_content_stream(*args, model=model, **kwargs)
            async for output in stream:
                yield output
        finally:
            _web_request.reset(token)

    def _set_web_request(
        self,
        model: Any,
        thinking_level: str | None,
        learning_mode: str | None,
    ):
        if thinking_level is None and learning_mode is None and _web_request.get():
            return _web_request.set(_web_request.get())

        level_id = resolve_thinking_level_id(thinking_level)
        if thinking_level is not None and level_id is None:
            raise ValueError("thinking_level 仅支持 standard/extended（或 标准/扩展）。")

        learning_config = resolve_learning_mode_config(learning_mode)
        if learning_mode is not None and learning_config is None:
            raise ValueError(f"learning_mode 仅支持 {supported_learning_modes()}。")

        mode_id = resolve_thinking_mode_id(model)
        options = WebRequestOptions(
            thinking_mode_id=mode_id,
            thinking_level_id=level_id,
            learning_mode_id=(
                int(learning_config["id"]) if learning_config is not None else None  # type: ignore[call-overload]
            ),
            learning_x9b_field=(
                str(learning_config["x9b_field"]) if learning_config is not None else None
            ),
            learning_x9b_value=(
                int(learning_config["x9b_value"]) if learning_config is not None else None  # type: ignore[call-overload]
            ),
        )
        if not any(
            (
                options.thinking_mode_id and options.thinking_level_id,
                options.learning_mode_id,
            )
        ):
            return _web_request.set(None)
        return _web_request.set(options)

    @contextmanager
    def thinking_scope(self, model: Any, thinking_level: str) -> Iterator[None]:
        """Keep a thinking level active through upstream helper workflows."""
        token = self._set_web_request(model, thinking_level, None)
        try:
            yield
        finally:
            _web_request.reset(token)

    def _with_learning_prompt(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        learning_mode: str | None,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        config = resolve_learning_mode_config(learning_mode)
        if not config:
            return args, kwargs

        prefix = str(config["prompt_prefix"])
        if "prompt" in kwargs:
            patched = dict(kwargs)
            patched["prompt"] = self._prefix_learning_prompt(prefix, patched.get("prompt"))
            return args, patched

        if args:
            patched_args = list(args)
            patched_args[0] = self._prefix_learning_prompt(prefix, patched_args[0])
            return tuple(patched_args), kwargs

        return args, kwargs

    @staticmethod
    def _prefix_learning_prompt(prefix: str, prompt: Any) -> Any:
        if not isinstance(prompt, str) or prompt.startswith(prefix):
            return prompt
        return f"{prefix}{prompt}"

    def _install_thinking_transport(self) -> None:
        session = self.client
        if not session or getattr(session, "_mcp_thinking_stream", False):
            return

        stream = session.stream

        def stream_with_thinking(method: str, url: str, *args: Any, **kwargs: Any):
            request = _web_request.get()
            data = kwargs.get("data")
            if request and url == Endpoint.GENERATE and isinstance(data, dict):
                kwargs["data"] = inject_web_request_options(data, request)
                headers = dict(kwargs.get("headers") or {})
                headers["x-goog-ext-73010990-jspb"] = "[0,0,0]"
                kwargs["headers"] = headers
            return stream(method, url, *args, **kwargs)

        session.stream = stream_with_thinking
        session._mcp_thinking_stream = True
