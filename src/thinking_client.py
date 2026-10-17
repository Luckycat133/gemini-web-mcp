"""Gemini Web request-scoped selectors and timeout compatibility."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, AsyncGenerator, Callable, Iterator

import orjson
from gemini_webapi import GeminiClient
from gemini_webapi.client import ChatSession
from gemini_webapi.constants import Endpoint
from gemini_webapi.types import ModelOutput

from .constants import (
    resolve_learning_mode_config,
    resolve_model_name,
    resolve_thinking_level_id,
    resolve_thinking_mode_id,
    supported_learning_modes,
)
from .domain.conversations import is_valid_remote_chat_id
from .infrastructure.web_request_contracts import (
    FEATURE_MODE_INDEX,
    NATIVE_TASK_MODE_ID,
    TASK_REQUEST_FLAG_INDEX,
    NativeMediaRequestShapeError,
    NativeTaskRequestShapeError,
    THINKING_LEVEL_INDEX,
    THINKING_MODE_INDEX,
    ThinkingRequestShapeError,
    native_media_mode_id,
    synchronize_thinking_headers,
)


@dataclass(frozen=True)
class WebRequestOptions:
    """Gemini Web fields that are not exposed by gemini-webapi yet."""

    thinking_mode_id: int | None = None
    thinking_level_id: int | None = None
    learning_mode_id: int | None = None
    learning_x9b_field: str | None = None
    learning_x9b_value: int | None = None
    media_mode_id: int | None = None
    task_mode: bool = False


_web_request: ContextVar[WebRequestOptions | None] = ContextVar(
    "gemini_web_request",
    default=None,
)

_request_timeout: ContextVar[tuple[object, float] | None] = ContextVar("gemini_request_timeout", default=None)
_timeout_initializing_task: ContextVar[tuple[int, int] | None] = ContextVar("gemini_timeout_initializing_task", default=None)
_generation_once: ContextVar[object | None] = ContextVar("gemini_generation_once", default=None)


@dataclass
class MediaRequestObservation:
    """Remember only the chat allocated by one fresh media request.

    The SDK rolls ChatSession metadata back after a failed stream. Recording
    the CID as metadata arrives preserves recovery evidence through that
    rollback, cancellation, or an outer asyncio.timeout.
    """

    on_chat_observed: Callable[[str], None] | None = None
    chat_id: str | None = field(default=None, init=False)
    _started: bool = field(default=False, init=False)

    def start(self) -> None:
        if self._started:
            raise ValueError("A media observation belongs to a single generation request.")
        self._started = True

    def observe(self, metadata: list[Any]) -> None:
        if metadata and is_valid_remote_chat_id(metadata[0]):
            changed = metadata[0] != self.chat_id
            self.chat_id = metadata[0]
            if changed and self.on_chat_observed is not None:
                self.on_chat_observed(self.chat_id)


class _OwnedMediaChatSession(ChatSession):
    """A blank request-owned SDK session with isolated, observable metadata."""

    __slots__ = ("_request_metadata", "_request_observation")

    def __init__(
        self, client: GeminiClient, observation: MediaRequestObservation, *, model: Any = None, gem: Any = None,
    ) -> None:
        # SDK2.0's DEFAULT_METADATA is shared mutable state (2.1 copies it).
        # Use properties backed by our own blank list in both versions, so a
        # previous chat can neither enter this request nor be mutated by it.
        self._request_metadata: list[Any] = ["", "", "", None, None, None, None, None, None, ""]
        self._request_observation = observation
        session_options = {}
        if model is not None:
            session_options["model"] = model
        if gem is not None:
            session_options["gem"] = gem
        super().__init__(client, **session_options)

    @property
    def observed_chat_id(self) -> str | None:
        """Source identity remains available after SDK metadata rollback."""
        return self._request_observation.chat_id

    @property
    def metadata(self) -> list[Any]:
        # The SDK retains this value as its rollback backup. It must not alias
        # the mutable request state that later stream frames update.
        return list(self._request_metadata)

    @metadata.setter
    def metadata(self, value: list[Any]) -> None:
        if not isinstance(value, list):
            return
        for index, item in enumerate(value[:10]):
            if item is not None:
                self._request_metadata[index] = item
        self._request_observation.observe(self._request_metadata)

    @property
    def cid(self) -> Any:
        return self._request_metadata[0]

    @cid.setter
    def cid(self, value: str) -> None:
        self._request_metadata[0] = value
        self._request_observation.observe(self._request_metadata)

    @property
    def rid(self) -> Any:
        return self._request_metadata[1]

    @rid.setter
    def rid(self, value: str) -> None:
        self._request_metadata[1] = value

    @property
    def rcid(self) -> Any:
        return self._request_metadata[2]

    @rcid.setter
    def rcid(self, value: str) -> None:
        self._request_metadata[2] = value


def _with_owned_media_session(
    client: GeminiClient,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    observation: MediaRequestObservation,
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if kwargs.get("chat") is not None or (len(args) > 4 and args[4] is not None):
        raise ValueError("Owned media generation cannot use an existing chat.")
    observation.start()
    chat = _OwnedMediaChatSession(client, observation)
    kwargs = {**kwargs, "current_retry": 0}
    if len(args) > 4:
        args = (*args[:4], chat, *args[5:])
    else:
        kwargs["chat"] = chat
    return args, kwargs


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


@contextmanager
def client_generation_once(client: object) -> Iterator[None]:
    """Prevent SDK submission retries inside one client-owned workflow.

    The SDK's generation decorator can repost an uncertain request after an
    API/parse failure. Research plan and confirmation are mutations whose
    recovery must read the existing source rather than generate again. This
    scope affects only generation; initialization and read-back keep their own
    retry policies. Child tasks inherit the scope without changing the shared
    client or another client's concurrent request.
    """
    token = _generation_once.set(client)
    try:
        yield
    finally:
        _generation_once.reset(token)


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
    if options.task_mode and (options.media_mode_id is not None or options.learning_mode_id is not None):
        raise ValueError("task_mode cannot be combined with media_mode or learning_mode.")
    feature_mode_id = NATIVE_TASK_MODE_ID if options.task_mode else options.media_mode_id
    shape_error = (
        NativeTaskRequestShapeError if options.task_mode
        else NativeMediaRequestShapeError if options.media_mode_id is not None
        else ThinkingRequestShapeError if options.thinking_level_id is not None else None
    )
    f_req = request_data.get("f.req")
    if not isinstance(f_req, str):
        if shape_error is not None:
            raise shape_error("Gemini Web request is missing its serialized f.req payload.")
        return request_data

    try:
        outer_request = orjson.loads(f_req)
    except orjson.JSONDecodeError as error:
        if shape_error is not None:
            raise shape_error("Gemini Web request envelope is not valid JSON.") from error
        raise
    if not isinstance(outer_request, list) or len(outer_request) < 2:
        if shape_error is not None:
            raise shape_error("Gemini Web request envelope shape changed.")
        return request_data

    inner_payload = outer_request[1]
    if not isinstance(inner_payload, str):
        if shape_error is not None:
            raise shape_error("Gemini Web request payload shape changed.")
        return request_data

    try:
        inner_request = orjson.loads(inner_payload)
    except orjson.JSONDecodeError as error:
        if shape_error is not None:
            raise shape_error("Gemini Web request payload is not valid JSON.") from error
        raise
    if not isinstance(inner_request, list):
        if shape_error is not None:
            raise shape_error("Gemini Web request payload shape changed.")
        return request_data

    if options.thinking_level_id is not None and (
        len(inner_request) < 69 or not isinstance(inner_request[0], list)
        or not inner_request[0] or not isinstance(inner_request[0][0], str)
    ):
        raise ThinkingRequestShapeError("Thinking StreamGenerate request shape changed.")

    if feature_mode_id is not None:
        feature_error = NativeTaskRequestShapeError if options.task_mode else NativeMediaRequestShapeError
        if (
            len(inner_request) <= FEATURE_MODE_INDEX
            or not isinstance(inner_request[0], list)
            or not inner_request[0]
            or not isinstance(inner_request[0][0], str)
        ):
            raise feature_error("Native feature StreamGenerate request shape changed.")
        if inner_request[FEATURE_MODE_INDEX] not in (None, feature_mode_id):
            raise feature_error("Native request conflicts with an existing feature selector.")
        inner_request[FEATURE_MODE_INDEX] = feature_mode_id

    if options.task_mode and len(inner_request) > TASK_REQUEST_FLAG_INDEX:
        task_flag = inner_request[TASK_REQUEST_FLAG_INDEX]
        if task_flag is not None and type(task_flag) is not bool:
            raise NativeTaskRequestShapeError("Native Task request flag shape changed.")

    required_length = 81 if feature_mode_id is None else len(inner_request)
    if options.task_mode:
        required_length = max(required_length, TASK_REQUEST_FLAG_INDEX + 1)
    if options.thinking_level_id is not None:
        required_length = max(required_length, 81)
    if options.learning_mode_id:
        required_length = max(required_length, 56)
    if len(inner_request) < required_length:
        inner_request.extend([None] * (required_length - len(inner_request)))

    if options.task_mode:
        # Match the explicit Task startup selection, including a false SDK
        # default if a future builder materializes this optional boolean.
        inner_request[TASK_REQUEST_FLAG_INDEX] = True

    if options.learning_mode_id:
        if not options.learning_x9b_field or options.learning_x9b_value is None:
            raise ValueError("learning mode transport metadata is incomplete")
        inner_request[54] = _encode_learning_x9b(
            options.learning_x9b_field,
            options.learning_x9b_value,
        )
        inner_request[55] = _encode_learning_goa(options.learning_mode_id)

    if options.thinking_level_id is not None:
        if options.thinking_mode_id is not None:
            inner_request[THINKING_MODE_INDEX] = options.thinking_mode_id
        inner_request[THINKING_LEVEL_INDEX] = options.thinking_level_id

    patched = dict(request_data)
    outer_request[1] = orjson.dumps(inner_request).decode("utf-8")
    patched["f.req"] = orjson.dumps(outer_request).decode("utf-8")
    return patched


class ThinkingLevelGeminiClient(GeminiClient):
    """Gemini client with scoped thinking, learning, and native media selectors."""

    def start_owned_chat(
        self, *, model: Any = None, gem: Any = None, on_chat_observed: Callable[[str], None] | None = None,
    ) -> ChatSession:
        """Allocate a blank observable session for one mutation workflow.

        This shares metadata ownership with media requests, independently of
        response parsing or artifact kind. Observations survive SDK rollback
        and are delivered before the next stream frame can fail.
        """
        observation = MediaRequestObservation(on_chat_observed=on_chat_observed)
        observation.start()
        return _OwnedMediaChatSession(self, observation, model=model, gem=gem)

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
        media_mode: str | None = None,
        task_mode: bool = False,
        media_observation: MediaRequestObservation | None = None,
        **kwargs: Any,
    ) -> ModelOutput:
        token = self._set_web_request(model, thinking_level, learning_mode, media_mode, task_mode)
        try:
            request = _web_request.get()
            if request and request.task_mode and media_observation is not None:
                raise ValueError("task_mode does not accept media artifact observation.")
            if media_observation is not None:
                args, kwargs = _with_owned_media_session(self, args, kwargs, media_observation)
            if _generation_once.get() is self or request and (request.media_mode_id is not None or request.task_mode):
                # gemini-webapi's running decorator consumes this before its
                # HTTP kwargs. A parse/API failure must not duplicate creation.
                kwargs["current_retry"] = 0
            args, kwargs = self._with_upstream_thinking(args, kwargs, GeminiClient.generate_content)
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
        media_mode: str | None = None,
        task_mode: bool = False,
        media_observation: MediaRequestObservation | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[ModelOutput, None]:
        token = self._set_web_request(model, thinking_level, learning_mode, media_mode, task_mode)
        try:
            request = _web_request.get()
            if request and request.task_mode and media_observation is not None:
                raise ValueError("task_mode does not accept media artifact observation.")
            if media_observation is not None:
                args, kwargs = _with_owned_media_session(self, args, kwargs, media_observation)
            if _generation_once.get() is self or request and (request.media_mode_id is not None or request.task_mode):
                kwargs["current_retry"] = 0
            args, kwargs = self._with_upstream_thinking(args, kwargs, GeminiClient.generate_content_stream)
            args, kwargs = self._with_learning_prompt(args, kwargs, learning_mode)
            if model is None:
                stream = super().generate_content_stream(*args, **kwargs)
            else:
                stream = super().generate_content_stream(*args, model=model, **kwargs)
            async for output in stream:
                yield output
        finally:
            _web_request.reset(token)

    @staticmethod
    def _with_upstream_thinking(
        args: tuple[Any, ...], kwargs: dict[str, Any], method: Any,
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        request = _web_request.get()
        parameters = inspect.signature(method).parameters
        if (
            request is not None
            and request.thinking_level_id is not None
            and "extended_thinking" in parameters
        ):
            # The SDK accepts only a bool. Seed its supported parameter, then
            # synchronize the exact enum in the final header and body below.
            # SDK2.0 must never receive an unknown HTTP argument.
            extended = request.thinking_level_id == 2
            positional = [
                item.name for item in parameters.values()
                if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                and item.name != "self"
            ]
            if "extended_thinking" in positional:
                index = positional.index("extended_thinking")
                if len(args) > index:
                    # Preserve the SDK's legal positional calling convention;
                    # adding a keyword would bind its eighth argument twice.
                    return (
                        (*args[:index], extended, *args[index + 1:]),
                        {key: value for key, value in kwargs.items() if key != "extended_thinking"},
                    )
            return args, {**kwargs, "extended_thinking": extended}
        return args, kwargs

    def _set_web_request(
        self,
        model: Any,
        thinking_level: str | None,
        learning_mode: str | None,
        media_mode: str | None = None,
        task_mode: bool = False,
    ):
        existing = _web_request.get()
        if thinking_level is None and learning_mode is None and media_mode is None and not task_mode and existing:
            return _web_request.set(existing)

        level_id = resolve_thinking_level_id(thinking_level)
        if thinking_level is not None and level_id is None:
            raise ValueError("thinking_level 仅支持 low/medium/high、standard/extended 或协议值 deep_think。")

        learning_config = resolve_learning_mode_config(learning_mode)
        if learning_mode is not None and learning_config is None:
            raise ValueError(f"learning_mode 仅支持 {supported_learning_modes()}。")

        mode_id = self._thinking_mode_id(model)
        if level_id is not None and isinstance(model, str) and model and mode_id is None:
            raise ValueError("The selected model has no observed thinking category; refresh the model registry.")
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
        if thinking_level is None and learning_mode is None and (media_mode is not None or task_mode) and existing:
            options = existing
        options = replace(options, media_mode_id=(
            native_media_mode_id(media_mode) if media_mode is not None
            else (existing.media_mode_id if existing else None)
        ), task_mode=task_mode or bool(existing and existing.task_mode))
        if options.task_mode and (options.media_mode_id is not None or options.learning_mode_id is not None):
            raise ValueError("task_mode cannot be combined with media_mode or learning_mode.")
        if options.media_mode_id is not None and options.learning_mode_id is not None:
            raise ValueError("media_mode cannot be combined with learning_mode.")
        if not any(
            (
                options.thinking_level_id is not None,
                options.learning_mode_id,
                options.media_mode_id,
                options.task_mode,
            )
        ):
            return _web_request.set(None)
        return _web_request.set(options)

    def _thinking_mode_id(self, model: Any) -> int | None:
        """Prefer this account's dynamic model category over compatibility data."""
        if isinstance(model, str):
            resolver = getattr(self, "resolve_model", None)
            list_models = getattr(self, "list_models", None)
            if callable(resolver) and callable(list_models):
                try:
                    models = list_models()
                except AttributeError:
                    models = None  # An uninitialized/older SDK has no registry.
                if models:
                    resolved = resolver(resolve_model_name(model))
                    mode_id = resolve_thinking_mode_id(resolved)
                    if mode_id is None:
                        raise ThinkingRequestShapeError("The discovered model has no valid thinking category.")
                    return mode_id
        return resolve_thinking_mode_id(model)

    @contextmanager
    def thinking_scope(self, model: Any, thinking_level: str) -> Iterator[None]:
        """Keep a thinking level active through upstream helper workflows."""
        token = self._set_web_request(model, thinking_level, None)
        try:
            yield
        finally:
            _web_request.reset(token)

    @contextmanager
    def media_scope(self, mode: str) -> Iterator[None]:
        """Keep an observed native media selector local to one workflow."""
        token = self._set_web_request(None, None, None, mode)
        try:
            yield
        finally:
            _web_request.reset(token)

    @contextmanager
    def task_scope(self) -> Iterator[None]:
        """Select native Task mode for one helper/session workflow."""
        token = self._set_web_request(None, None, None, task_mode=True)
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
            if url == Endpoint.GENERATE and _generation_once.get() is self and "current_retry" in kwargs:
                raise ValueError("Upstream generation retry control was not consumed.")
            if request and url == Endpoint.GENERATE:
                if request.task_mode and "current_retry" in kwargs:
                    raise NativeTaskRequestShapeError("Upstream Task retry control was not consumed.")
                if request.media_mode_id is not None and "current_retry" in kwargs:
                    raise NativeMediaRequestShapeError("Upstream native media retry control was not consumed.")
                if not isinstance(data, dict):
                    if request.task_mode:
                        raise NativeTaskRequestShapeError("Native Task StreamGenerate form data shape changed.")
                    if request.media_mode_id is not None:
                        raise NativeMediaRequestShapeError("Native media StreamGenerate form data shape changed.")
                    if request.thinking_level_id is not None:
                        raise ThinkingRequestShapeError("Thinking StreamGenerate form data shape changed.")
                    return stream(method, url, *args, **kwargs)
                kwargs["data"] = inject_web_request_options(data, request)
                if request.thinking_level_id is not None or request.learning_mode_id:
                    headers = dict(kwargs.get("headers") or {})
                    if request.thinking_level_id is not None:
                        headers = synchronize_thinking_headers(
                            headers, mode_id=request.thinking_mode_id, level_id=request.thinking_level_id,
                        )
                    headers["x-goog-ext-73010990-jspb"] = "[0,0,0]"
                    kwargs["headers"] = headers
            return stream(method, url, *args, **kwargs)

        session.stream = stream_with_thinking
        session._mcp_thinking_stream = True
