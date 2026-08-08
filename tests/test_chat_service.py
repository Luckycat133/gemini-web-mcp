"""P0.4 shared chat service and adapter-parity tests."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from src.adapters.mcp_sdk import MCPServer, TextContent

import src.skill_server as skill_server
import src.tools.chat as chat_tools
from src.domain import DomainErrorCode, DomainResult, StreamChunkSemantics, StreamDelivery
from src.services import (
    ChatOperationData,
    ChatRequest,
    ChatService,
    ChatServiceDependencies,
    CleanupStrategy,
    SessionMessageRequest,
    StartSessionRequest,
)
from src.session_manager import SessionService


class _FakeClient:
    def __init__(self, events: list[Any]):
        self.events = events
        self.response = SimpleNamespace(
            text="answer",
            images=[],
            videos=[],
            media=[],
            metadata=["c_service"],
        )

    async def generate_content(self, **kwargs: Any) -> Any:
        self.events.append(("generate", kwargs))
        return self.response

    async def generate_content_stream(self, **kwargs: Any):
        self.events.append(("generate_stream", kwargs))
        for text in ("first", "second"):
            yield SimpleNamespace(text=text, text_delta=text)

    def start_chat(self, **kwargs: Any) -> Any:
        self.events.append(("start_chat", kwargs))
        return SimpleNamespace(cid="c_session")


def _make_service(
    *,
    response_cleanup_id: str | None = "c_service",
    session_service: SessionService | None = None,
) -> tuple[ChatService, _FakeClient, list[Any], SessionService]:
    events: list[Any] = []
    sessions = session_service or SessionService(id_factory=lambda: "sess_service")
    client = _FakeClient(events)

    async def initialize_client() -> Any:
        events.append("initialize")
        return client

    async def cleanup_due_remote_chats(value: Any) -> int:
        assert value is client
        events.append("cleanup_due")
        return 0

    def schedule_response_cleanup(response: Any, **kwargs: Any) -> str | None:
        events.append(("cleanup_response", response, kwargs))
        return response_cleanup_id

    def schedule_chat_cleanup(chat_id: str | None, **kwargs: Any) -> None:
        events.append(("cleanup_chat", chat_id, kwargs))

    service = ChatService(
        ChatServiceDependencies(
            client_provider=lambda: events.append("provider") or client,
            client_initializer=initialize_client,
            cleanup_due_remote_chats=cleanup_due_remote_chats,
            create_session=sessions.create_session,
            lookup_session=sessions.lookup_session,
            send_session_message=sessions.send_message,
            send_session_message_stream=sessions.send_message_stream,
            schedule_response_cleanup=schedule_response_cleanup,
            schedule_chat_cleanup=schedule_chat_cleanup,
            normalize_model=lambda model: {"f": "flash"}.get(model, model),
            resolve_model=lambda model: f"web-{model}",
        )
    )
    return service, client, events, sessions


@pytest.mark.parametrize(
    ("include_gem", "include_temporary", "expected_optional"),
    [
        (True, True, {"gem": "gem-1", "temporary": True}),
        (False, False, {}),
    ],
)
def test_generate_preserves_adapter_specific_request_shape(
    include_gem: bool,
    include_temporary: bool,
    expected_optional: dict[str, Any],
):
    service, client, events, _sessions = _make_service()

    result = asyncio.run(
        service.generate(
            ChatRequest(
                message="hello",
                model="f",
                thinking_level="extended",
                learning_mode="quiz",
                files=("/tmp/image.png",),
                gem_id="gem-1",
                temporary=True,
                retain_chat=True,
                delete_after_seconds=60,
                cleanup_source="gemini_chat" if include_gem else "skill_chat",
                include_gem_argument=include_gem,
                include_temporary_argument=include_temporary,
            )
        )
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.response is client.response
    assert events[:3] == ["provider", "initialize", "cleanup_due"]
    request_kwargs = events[3][1]
    assert request_kwargs == {
        "prompt": "hello",
        "files": ["/tmp/image.png"],
        "model": "web-flash",
        "thinking_level": "extended",
        "learning_mode": "quiz",
        **expected_optional,
    }
    assert events[4][2] == {
        "retain_chat": True,
        "delete_after_seconds": 60,
        "source": "gemini_chat" if include_gem else "skill_chat",
    }
    payload = result.to_dict()
    assert payload["data"]["effective_model"] == "web-flash"
    assert "response" not in payload["data"]
    json.dumps(payload)


@pytest.mark.parametrize("include_gem", [True, False])
def test_start_session_uses_shared_lifecycle_and_public_result(include_gem: bool):
    service, _client, events, sessions = _make_service()

    result = asyncio.run(
        service.start_session(
            StartSessionRequest(
                model="f",
                thinking_level="extended",
                learning_mode="quiz",
                gem_id="gem-1",
                temporary=True,
                retain_chat=True,
                delete_after_seconds=90,
                include_gem_argument=include_gem,
            )
        )
    )

    assert result.ok is True
    assert result.data is not None
    expected_start = {"model": "web-flash"}
    if include_gem:
        expected_start["gem"] = "gem-1"
    assert events[3] == ("start_chat", expected_start)
    assert result.data.session_id == "sess_service"
    state = sessions.lookup_session("sess_service").session
    assert state is not None
    assert state.model == "flash"
    assert state.thinking_level == "extended"
    assert state.learning_mode == "quiz"
    assert state.temporary is True
    payload = result.to_dict()
    assert payload["data"]["session_state"]["session_id"] == "sess_service"
    assert "session" not in payload["data"]["session_state"]


def test_missing_session_returns_typed_failure_without_client_side_effects():
    service, _client, events, _sessions = _make_service()

    result = asyncio.run(
        service.send_session(
            SessionMessageRequest(
                session_id="sess_missing",
                message="hello",
                prepare_client=True,
            )
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.SESSION_NOT_FOUND
    assert result.meta.verification_status == "local_state_absent"
    assert events == []


def test_session_request_fallback_and_cleanup_strategy_are_centralized():
    service, _client, events, sessions = _make_service(response_cleanup_id=None)

    class RuntimeSession:
        cid = "c_session"

        async def send_message(self, **kwargs: Any) -> Any:
            events.append(("session_send", kwargs))
            return SimpleNamespace(text="session answer")

    sessions.store_session(
        "sess_shared",
        RuntimeSession(),
        model="f",
        thinking_level="",
        learning_mode="quiz",
        temporary=True,
        retain_chat=True,
        delete_after_seconds=120,
    )

    result = asyncio.run(
        service.send_session(
            SessionMessageRequest(
                session_id="sess_shared",
                message="continue",
                thinking_level="extended",
                prepare_client=True,
                include_temporary=False,
                fallback_empty_thinking_level=True,
                cleanup_strategy=CleanupStrategy.RESPONSE_THEN_SESSION,
                cleanup_source="compact:session",
            )
        )
    )

    assert result.ok is True
    assert result.data is not None
    assert events[:3] == ["provider", "initialize", "cleanup_due"]
    assert events[3] == (
        "session_send",
        {
            "prompt": "continue",
            "files": None,
            "thinking_level": "extended",
            "learning_mode": "quiz",
        },
    )
    assert events[4][0] == "cleanup_response"
    assert events[5] == (
        "cleanup_chat",
        "c_session",
        {
            "retain_chat": True,
            "delete_after_seconds": 120,
            "source": "compact:session",
        },
    )
    payload = result.to_dict()
    assert payload["data"]["session_id"] == "sess_shared"
    assert payload["data"]["remote_chat_id"] == "c_session"
    assert "response" not in payload["data"]
    assert "session" not in payload["data"]["session_state"]


def test_generate_stream_collects_text_and_uses_final_response_for_cleanup():
    service, _client, events, _sessions = _make_service()

    result = asyncio.run(
        service.generate_stream(ChatRequest(message="stream"))
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.stream is not None
    assert result.data.stream.delivery is StreamDelivery.COLLECTED
    assert result.data.stream.chunk_semantics is StreamChunkSemantics.DELTA
    assert result.data.stream.chunk_count == 2
    assert result.data.stream_text == "firstsecond"
    assert [response.text for response in result.data.responses] == ["first", "second"]
    assert events[-1][0] == "cleanup_response"
    assert events[-1][1].text == "second"
    payload = result.to_dict()
    assert payload["data"]["stream"]["delivery"] == "collected"
    assert payload["data"]["stream"]["chunk_semantics"] == "delta"
    assert "streamed" not in payload["data"]
    assert "responses" not in payload["data"]
    assert "stream_text" not in payload["data"]


def test_generate_stream_cancellation_propagates_and_closes_upstream_iterator():
    service, client, events, _sessions = _make_service()
    started = asyncio.Event()
    closed = asyncio.Event()

    async def blocked_stream(**_kwargs: Any):
        try:
            started.set()
            await asyncio.Event().wait()
            yield SimpleNamespace(text_delta="late")
        finally:
            closed.set()

    client.generate_content_stream = blocked_stream  # type: ignore[method-assign]

    async def run() -> None:
        task = asyncio.create_task(service.generate_stream(ChatRequest(message="cancel")))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), timeout=1)

    asyncio.run(run())
    assert not any(isinstance(event, tuple) and event[0] == "cleanup_response" for event in events)


def test_session_stream_normalizes_cumulative_chunks_before_returning_once():
    service, _client, _events, sessions = _make_service()

    class RuntimeSession:
        cid = "c_session_stream"

        async def send_message_stream(self, **_kwargs: Any):
            for text in ("H", "He", "Hello"):
                yield SimpleNamespace(text=text)

    sessions.store_session("sess_stream", RuntimeSession(), model="flash")

    result = asyncio.run(
        service.send_session_stream(
            SessionMessageRequest(
                session_id="sess_stream",
                message="hello",
                cleanup_strategy=CleanupStrategy.RESPONSE,
            )
        )
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.stream_text == "Hello"
    assert result.data.stream is not None
    assert result.data.stream.chunk_semantics is StreamChunkSemantics.CUMULATIVE
    assert result.data.stream.chunk_count == 3


def test_compact_cleanup_adapter_forwards_shared_retention_policy(monkeypatch):
    calls: list[tuple[str, Any, dict[str, Any]]] = []

    def schedule_response(response: Any, **kwargs: Any) -> str:
        calls.append(("response", response, kwargs))
        return "c_policy"

    def schedule_chat(chat_id: str, **kwargs: Any) -> None:
        calls.append(("chat", chat_id, kwargs))

    monkeypatch.setattr(
        skill_server,
        "schedule_remote_chat_cleanup_from_response",
        schedule_response,
    )
    monkeypatch.setattr(
        skill_server,
        "schedule_remote_chat_cleanup",
        schedule_chat,
    )
    response = SimpleNamespace(metadata=["c_policy"])

    assert skill_server._schedule_compact_response_cleanup(
        response,
        retain_chat=True,
        delete_after_seconds=90,
        source="compact",
    ) == "c_policy"
    skill_server._schedule_compact_chat_cleanup(
        "c_policy",
        retain_chat=True,
        delete_after_seconds=90,
        source="compact",
    )

    assert calls == [
        (
            "response",
            response,
            {
                "retain_chat": True,
                "delete_after_seconds": 90,
                "source": "compact",
            },
        ),
        (
            "chat",
            "c_policy",
            {
                "retain_chat": True,
                "delete_after_seconds": 90,
                "source": "compact",
            },
        ),
    ]


def _domain_payload(content: TextContent) -> dict[str, Any]:
    assert content.meta is not None
    payload = content.meta["domain_result"]
    assert isinstance(payload, dict)
    return payload


def test_primary_and_compact_chat_adapters_have_success_contract_parity(monkeypatch):
    upstream_calls: list[dict[str, Any]] = []
    response = SimpleNamespace(
        text="same answer",
        images=[],
        videos=[],
        media=[],
        metadata=["c_shared"],
    )

    class Client:
        async def generate_content(self, **kwargs: Any) -> Any:
            upstream_calls.append(kwargs)
            return response

    client = Client()

    async def initialize_client() -> Any:
        return client

    async def cleanup_due_remote_chats(_client: Any) -> int:
        return 0

    for module in (chat_tools, skill_server):
        monkeypatch.setattr(module, "get_gemini_client", lambda: client)
        monkeypatch.setattr(module, "initialize_client", initialize_client)
        monkeypatch.setattr(module, "cleanup_due_remote_chats", cleanup_due_remote_chats)
        monkeypatch.setattr(module, "resolve_model_name", lambda _model: "web-flash")
    monkeypatch.setattr(
        chat_tools,
        "schedule_remote_chat_cleanup_from_response",
        lambda *_args, **_kwargs: "c_shared",
    )
    monkeypatch.setattr(
        skill_server,
        "schedule_remote_chat_cleanup_from_response",
        lambda _response, source, **_kwargs: "c_shared",
    )

    primary = MCPServer("chat-service-parity")
    chat_tools.register_chat_tools(primary)

    async def run():
        primary_result = await primary.call_tool(
            "gemini_chat",
            {"message": "hello", "model": "flash"},
        )
        compact_content = await skill_server.chat(message="hello", model="flash")
        return primary_result.content, compact_content

    primary_content, compact_content = asyncio.run(run())
    primary_payload = _domain_payload(primary_content[0])
    compact_payload = _domain_payload(compact_content[0])

    assert primary_content[0].text == compact_content[0].text
    expected_lifecycle = {
        "session_id": None,
        "upstream_chat_id": "c_shared",
        "session_state": "stateless",
        "retain_chat": False,
        "delete_after_seconds": None,
        "cleanup": {
            "state": "pending",
            "upstream_chat_id": "c_shared",
            "attempts": 0,
            "diagnostic_id": None,
            "idempotent": False,
        },
    }
    assert primary_payload["data"] == {
        "model": "flash",
        "resolved_model": "web-flash",
        "temporary": False,
        "lifecycle": expected_lifecycle,
    }
    assert compact_payload["data"] == {
        "model": "flash",
        "resolved_model": "web-flash",
        "lifecycle": expected_lifecycle,
    }
    for payload in (primary_payload, compact_payload):
        assert payload["ok"] is True
        assert payload["meta"]["requested_backend"] == "flash"
        assert payload["meta"]["effective_backend"] == "web-flash"
        assert payload["meta"]["verification_status"] == "upstream_response_received"
        assert payload["meta"]["details"]["service"] == "chat"
        assert payload["meta"]["details"]["lifecycle"] == expected_lifecycle

    common_kwargs = {
        "prompt": "hello",
        "files": None,
        "model": "web-flash",
        "thinking_level": "standard",
    }
    assert upstream_calls == [
        {**common_kwargs, "gem": None, "temporary": False},
        common_kwargs,
    ]


def test_both_chat_adapters_delegate_to_the_shared_service(monkeypatch):
    requests: list[ChatRequest] = []

    async def fake_generate(_service: ChatService, request: ChatRequest):
        requests.append(request)
        return DomainResult.success(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=request.model,
                effective_model="web-flash",
                temporary=request.temporary,
                response=SimpleNamespace(text="delegated", metadata=[]),
            ),
            requested_backend=request.model,
            effective_backend="web-flash",
            verification_status="upstream_response_received",
        )

    monkeypatch.setattr(ChatService, "generate", fake_generate)
    primary = MCPServer("chat-service-delegation")
    chat_tools.register_chat_tools(primary)

    async def run():
        await primary.call_tool("gemini_chat", {"message": "primary"})
        await skill_server.chat(message="compact")

    asyncio.run(run())

    assert [request.message for request in requests] == ["primary", "compact"]
    assert requests[0].include_gem_argument is True
    assert requests[0].include_temporary_argument is True
    assert requests[1].include_gem_argument is False
    assert requests[1].include_temporary_argument is False


def test_chat_tool_argument_schemas_remain_compatible():
    primary = MCPServer("chat-schema-compatibility")
    chat_tools.register_chat_tools(primary)

    async def schemas():
        primary_tools = {tool.name: tool for tool in await primary.list_tools()}
        compact_tools = {tool.name: tool for tool in await skill_server.mcp.list_tools()}
        return {
            name: set(tool.input_schema.get("properties", {}))
            for name, tool in {**primary_tools, **compact_tools}.items()
            if name in {"gemini_chat", "gemini_start_chat", "gemini_send_message", "chat", "session"}
        }

    assert asyncio.run(schemas()) == {
        "gemini_chat": {
            "message",
            "model",
            "thinking_level",
            "learning_mode",
            "image_paths",
            "gem_id",
            "temporary",
            "retain_chat",
            "delete_after_seconds",
        },
        "gemini_start_chat": {
            "model",
            "thinking_level",
            "learning_mode",
            "gem_id",
            "temporary",
            "retain_chat",
            "delete_after_seconds",
        },
        "gemini_send_message": {
            "session_id",
            "message",
            "image_paths",
            "learning_mode",
            "temporary",
            "retain_chat",
            "delete_after_seconds",
        },
        "chat": {
            "message",
            "model",
            "thinking_level",
            "learning_mode",
            "image_path",
            "session_id",
        },
        "session": {
            "action",
            "session_id",
            "message",
            "model",
            "thinking_level",
            "learning_mode",
            "image_path",
        },
    }
