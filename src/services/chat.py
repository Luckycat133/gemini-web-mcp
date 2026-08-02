"""Shared chat application service for the primary and compact MCP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..domain import DomainResult
from ..session_manager import SessionData, SessionOperationResult


class CleanupStrategy(str, Enum):
    """Select the existing remote-chat cleanup behavior for an adapter path."""

    RESPONSE = "response"
    SESSION = "session"
    RESPONSE_THEN_SESSION = "response_then_session"


@dataclass(frozen=True)
class ChatServiceDependencies:
    client_provider: Callable[[], Any]
    client_initializer: Callable[[], Awaitable[Any]]
    cleanup_due_remote_chats: Callable[[Any], Awaitable[int]]
    create_session: Callable[..., SessionOperationResult]
    lookup_session: Callable[[str], SessionOperationResult]
    send_session_message: Callable[..., Awaitable[SessionOperationResult]]
    send_session_message_stream: Callable[..., Awaitable[SessionOperationResult]]
    schedule_response_cleanup: Callable[..., str | None]
    schedule_chat_cleanup: Callable[..., None]
    normalize_model: Callable[[str], str]
    resolve_model: Callable[[str], str]


@dataclass(frozen=True)
class ChatRequest:
    message: str
    model: str = "flash"
    thinking_level: str = "standard"
    learning_mode: str | None = None
    files: tuple[str, ...] = ()
    gem_id: str | None = None
    temporary: bool = False
    retain_chat: bool = False
    delete_after_seconds: int | None = None
    cleanup_source: str = "gemini_chat"
    include_gem_argument: bool = True
    include_temporary_argument: bool = True


@dataclass(frozen=True)
class StartSessionRequest:
    model: str = "flash"
    thinking_level: str = "standard"
    learning_mode: str | None = None
    gem_id: str | None = None
    temporary: bool = False
    retain_chat: bool = False
    delete_after_seconds: int | None = None
    include_gem_argument: bool = True


@dataclass(frozen=True)
class SessionMessageRequest:
    session_id: str
    message: str
    files: tuple[str, ...] = ()
    learning_mode: str | None = None
    thinking_level: str = "standard"
    temporary: bool | None = None
    retain_chat: bool | None = None
    delete_after_seconds: int | None = None
    prepare_client: bool = False
    include_temporary: bool = True
    fallback_empty_thinking_level: bool = False
    cleanup_strategy: CleanupStrategy = CleanupStrategy.SESSION
    cleanup_source: str = ""


@dataclass(frozen=True)
class ChatOperationData:
    requested_model: str
    normalized_model: str
    effective_model: str
    session_id: str | None = None
    session_state: SessionData | None = None
    temporary: bool = False
    streamed: bool = False
    remote_chat_id: str | None = None
    response: Any = field(default=None, metadata={"domain_exclude": True})
    responses: tuple[Any, ...] = field(
        default=(),
        metadata={"domain_exclude": True},
    )
    stream_text: str = field(default="", metadata={"domain_exclude": True})


class ChatService:
    """Own request construction and execution below both MCP presentation layers."""

    def __init__(self, dependencies: ChatServiceDependencies):
        self._dependencies = dependencies

    async def generate(self, request: ChatRequest) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(request.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        client = await self._prepare_client()
        request_kwargs = self._one_shot_kwargs(
            request,
            effective_model=effective_model,
        )
        response = await client.generate_content(**request_kwargs)
        remote_chat_id = self._dependencies.schedule_response_cleanup(
            response,
            retain_chat=request.retain_chat,
            delete_after_seconds=request.delete_after_seconds,
            source=request.cleanup_source,
        )
        return self._success(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                temporary=request.temporary,
                remote_chat_id=remote_chat_id,
                response=response,
            )
        )

    async def generate_stream(
        self,
        request: ChatRequest,
        *,
        text_piece: Callable[[Any], str],
    ) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(request.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        client = await self._prepare_client()
        request_kwargs = self._one_shot_kwargs(
            request,
            effective_model=effective_model,
        )
        responses: list[Any] = []
        full_text = ""
        async for response in client.generate_content_stream(**request_kwargs):
            responses.append(response)
            full_text += text_piece(response)

        final_response = responses[-1] if responses else None
        remote_chat_id = None
        if final_response is not None:
            remote_chat_id = self._dependencies.schedule_response_cleanup(
                final_response,
                retain_chat=request.retain_chat,
                delete_after_seconds=request.delete_after_seconds,
                source=request.cleanup_source,
            )
        return self._success(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                temporary=request.temporary,
                streamed=True,
                remote_chat_id=remote_chat_id,
                response=final_response,
                responses=tuple(responses),
                stream_text=full_text,
            )
        )

    async def start_session(
        self,
        request: StartSessionRequest,
    ) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(request.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        client = await self._prepare_client()
        start_kwargs: dict[str, Any] = {"model": effective_model}
        if request.include_gem_argument:
            start_kwargs["gem"] = request.gem_id
        session = client.start_chat(**start_kwargs)
        created = self._dependencies.create_session(
            session,
            normalized_model,
            thinking_level=request.thinking_level,
            learning_mode=request.learning_mode,
            temporary=request.temporary,
            retain_chat=request.retain_chat,
            delete_after_seconds=request.delete_after_seconds,
        )
        if not created.ok or created.session is None:
            return self._copy_failure(created)
        state = created.session
        return self._success(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                session_id=state.session_id,
                session_state=state,
                temporary=state.temporary,
                remote_chat_id=self._session_chat_id(state),
            ),
            request_id=created.meta.request_id,
            verification_status=created.meta.verification_status,
        )

    async def send_session(
        self,
        request: SessionMessageRequest,
    ) -> DomainResult[ChatOperationData]:
        lookup = self._dependencies.lookup_session(request.session_id)
        if not lookup.ok or lookup.session is None:
            return self._copy_failure(lookup)
        state = lookup.session
        if request.prepare_client:
            await self._prepare_client()

        request_kwargs = self._session_request_kwargs(request, state)
        sent = await self._dependencies.send_session_message(
            request.session_id,
            **request_kwargs,
        )
        if not sent.ok or sent.session is None:
            return self._copy_failure(sent)
        state = sent.session
        response = sent.response
        remote_chat_id = self._schedule_session_cleanup(request, state, response)
        return self._session_success(
            request,
            state,
            response=response,
            remote_chat_id=remote_chat_id,
            request_id=sent.meta.request_id,
            verification_status=sent.meta.verification_status,
        )

    async def send_session_stream(
        self,
        request: SessionMessageRequest,
        *,
        text_piece: Callable[[Any], str],
    ) -> DomainResult[ChatOperationData]:
        lookup = self._dependencies.lookup_session(request.session_id)
        if not lookup.ok or lookup.session is None:
            return self._copy_failure(lookup)
        state = lookup.session
        if request.prepare_client:
            await self._prepare_client()

        request_kwargs = self._session_request_kwargs(request, state)
        streamed = await self._dependencies.send_session_message_stream(
            request.session_id,
            **request_kwargs,
        )
        if not streamed.ok or streamed.session is None:
            return self._copy_failure(streamed)
        state = streamed.session
        responses = tuple(streamed.response or ())
        full_text = "".join(text_piece(response) for response in responses)
        final_response = responses[-1] if responses else None
        remote_chat_id = self._schedule_session_cleanup(
            request,
            state,
            final_response,
        )
        return self._session_success(
            request,
            state,
            response=final_response,
            responses=responses,
            stream_text=full_text,
            streamed=True,
            remote_chat_id=remote_chat_id,
            request_id=streamed.meta.request_id,
            verification_status=streamed.meta.verification_status,
        )

    async def _prepare_client(self) -> Any:
        client = self._dependencies.client_provider()
        await self._dependencies.client_initializer()
        await self._dependencies.cleanup_due_remote_chats(client)
        return client

    @staticmethod
    def _one_shot_kwargs(
        request: ChatRequest,
        *,
        effective_model: str,
    ) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "prompt": request.message,
            "files": list(request.files) or None,
            "model": effective_model,
            "thinking_level": request.thinking_level,
        }
        if request.include_gem_argument:
            request_kwargs["gem"] = request.gem_id
        if request.include_temporary_argument:
            request_kwargs["temporary"] = request.temporary
        if request.learning_mode:
            request_kwargs["learning_mode"] = request.learning_mode
        return request_kwargs

    @staticmethod
    def _session_request_kwargs(
        request: SessionMessageRequest,
        state: SessionData,
    ) -> dict[str, Any]:
        thinking_level = state.thinking_level
        if request.fallback_empty_thinking_level:
            thinking_level = thinking_level or request.thinking_level
        request_kwargs: dict[str, Any] = {
            "prompt": request.message,
            "files": list(request.files) or None,
            "thinking_level": thinking_level,
        }
        if request.include_temporary:
            request_kwargs["temporary"] = state.temporary if request.temporary is None else request.temporary
        learning_mode = request.learning_mode or state.learning_mode
        if learning_mode:
            request_kwargs["learning_mode"] = learning_mode
        return request_kwargs

    def _schedule_session_cleanup(
        self,
        request: SessionMessageRequest,
        state: SessionData,
        response: Any,
    ) -> str | None:
        retain_chat = state.retain_chat if request.retain_chat is None else request.retain_chat
        delete_after_seconds = request.delete_after_seconds
        if delete_after_seconds is None:
            delete_after_seconds = state.delete_after_seconds

        if request.cleanup_strategy in {
            CleanupStrategy.RESPONSE,
            CleanupStrategy.RESPONSE_THEN_SESSION,
        }:
            remote_chat_id = self._dependencies.schedule_response_cleanup(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source=request.cleanup_source,
            )
            if remote_chat_id or request.cleanup_strategy == CleanupStrategy.RESPONSE:
                return remote_chat_id

        remote_chat_id = self._session_chat_id(state)
        self._dependencies.schedule_chat_cleanup(
            remote_chat_id,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=request.cleanup_source,
        )
        return remote_chat_id

    def _session_success(
        self,
        request: SessionMessageRequest,
        state: SessionData,
        *,
        response: Any,
        responses: Sequence[Any] = (),
        stream_text: str = "",
        streamed: bool = False,
        remote_chat_id: str | None,
        request_id: str,
        verification_status: str,
    ) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(state.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        temporary = state.temporary if request.temporary is None else request.temporary
        return self._success(
            ChatOperationData(
                requested_model=state.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                session_id=state.session_id,
                session_state=state,
                temporary=temporary,
                streamed=streamed,
                remote_chat_id=remote_chat_id,
                response=response,
                responses=tuple(responses),
                stream_text=stream_text,
            ),
            request_id=request_id,
            verification_status=verification_status,
        )

    @staticmethod
    def _session_chat_id(state: SessionData) -> str | None:
        return getattr(state.session, "cid", None) or state.upstream_chat_id

    @staticmethod
    def _copy_failure(
        result: DomainResult[Any],
    ) -> DomainResult[ChatOperationData]:
        assert result.error is not None
        return DomainResult(
            ok=False,
            data=None,
            error=result.error,
            warnings=result.warnings,
            meta=result.meta,
        )

    @staticmethod
    def _success(
        data: ChatOperationData,
        *,
        request_id: str | None = None,
        verification_status: str = "upstream_response_received",
    ) -> DomainResult[ChatOperationData]:
        return DomainResult.success(
            data,
            request_id=request_id,
            requested_backend=data.requested_model,
            effective_backend=data.effective_model,
            verification_status=verification_status,
            details={"service": "chat"},
        )
