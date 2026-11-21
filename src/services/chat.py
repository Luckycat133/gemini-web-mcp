"""Shared chat application service for the primary and compact MCP adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, cast

from ..domain import (
    CleanupObservation,
    CleanupState,
    ConversationLifecycleMetadata,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    SessionLifecycleState,
    StreamCollectionMetadata,
    cleanup_observation_for_policy,
    is_valid_remote_chat_id,
)
from ..session_manager import SessionData, SessionOperationResult
from ..infrastructure.account_identity import current_account_key
from ..constants import DEFAULT_SESSION_MAX_AGE_SECONDS, is_generation_failure_reply
from .artifacts import extract_response_artifacts
from .cleanup_scope import observe_cleanup_frames
from .client_timeouts import client_timeout_scope
from .deadlines import await_before_deadline
from .streams import StreamTextAccumulator
from .model_policy import ModelSelection, model_policy_scope, model_selection_from_response

CHAT_GENERATION_TIMEOUT_SECONDS = 300


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
    schedule_response_cleanup: Callable[..., CleanupObservation | str | None]
    schedule_chat_cleanup: Callable[..., CleanupObservation | None]
    normalize_model: Callable[[str], str]
    resolve_model: Callable[[str], str]
    finalize_response_cleanup: Callable[..., Awaitable[CleanupObservation]] | None = None
    account_key_provider: Callable[[], str | None] = current_account_key


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
    recover_only: bool = False
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
    lifecycle: ConversationLifecycleMetadata = field(
        default_factory=ConversationLifecycleMetadata.stateless,
    )
    session_id: str | None = None
    session_state: SessionData | None = None
    temporary: bool = False
    stream: StreamCollectionMetadata | None = None
    remote_chat_id: str | None = None
    model_selection: ModelSelection | None = None
    response: Any = field(default=None, metadata={"domain_exclude": True})
    responses: tuple[Any, ...] = field(
        default=(),
        metadata={"domain_exclude": True},
    )
    stream_text: str = field(default="", metadata={"domain_exclude": True})


class _FrameCleanup:
    """Keep a task-local durable fallback for every observed conversation."""

    def __init__(self, service: "ChatService", *, retain_chat: bool, delay: int | None, source: str, owner: str,
                 reschedule_pending: bool = False):
        self.service = service
        self.retain_chat = retain_chat
        self.delay = delay
        self.source = source
        self.owner = owner
        self.reschedule_pending = reschedule_pending
        self.registered: set[str] = set()
        self.failed: set[str] = set()

    def observe(self, value: Any) -> None:
        cid = self.service._response_chat_id(value)
        if not cid or cid in self.registered:
            return
        try:
            options: dict[str, Any] = {"retain_chat": self.retain_chat, "delete_after_seconds": self.delay,
                                       "source": self.source}
            if self.reschedule_pending:
                options["reschedule_pending"] = True
            if self.service._dependencies.account_key_provider() != self.owner:
                options["_owner_account"] = self.owner
            result = self.service._dependencies.schedule_chat_cleanup(cid, **options)
            if isinstance(result, CleanupObservation) and result.state in {CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE}:
                self.failed.add(cid)
                return
        except Exception:
            self.failed.add(cid)
            return
        self.failed.discard(cid)
        self.registered.add(cid)

    def fallback(self, *, immediate: bool, delete_after_seconds: int | None = None) -> None:
        for cid in self.registered | self.failed:
            try:
                options: dict[str, Any] = {"retain_chat": self.retain_chat,
                                           "delete_after_seconds": delete_after_seconds if immediate else self.delay,
                                           "source": self.source}
                if immediate or self.reschedule_pending:
                    options["reschedule_pending"] = True
                if self.service._dependencies.account_key_provider() != self.owner:
                    options["_owner_account"] = self.owner
                result = self.service._dependencies.schedule_chat_cleanup(cid, **options)
                if isinstance(result, CleanupObservation) and result.state in {CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE}:
                    self.failed.add(cid)
                else:
                    self.failed.discard(cid)
            except Exception:
                self.failed.add(cid)


class ChatService:
    """Own request construction and execution below both MCP presentation layers."""

    def __init__(self, dependencies: ChatServiceDependencies):
        self._dependencies = dependencies

    @staticmethod
    def _account_failure() -> DomainResult[ChatOperationData]:
        return DomainResult.failure(DomainErrorCode.AUTH_REQUIRED, "The conversation account could not be verified.",
                                    verification_status="account_mismatch_or_unbound")

    async def generate(self, request: ChatRequest) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(request.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        client = await self._prepare_client()
        owner = self._dependencies.account_key_provider()
        if not owner:
            return self._account_failure()
        request_kwargs = self._one_shot_kwargs(
            request,
            effective_model=effective_model,
        )
        observer = _FrameCleanup(self, retain_chat=request.retain_chat,
                                 delay=(request.delete_after_seconds if request.retain_chat else
                                        max(DEFAULT_SESSION_MAX_AGE_SECONDS, request.delete_after_seconds or 0)),
                                 source=request.cleanup_source, owner=owner)
        factory = getattr(type(client), "create_cleanup_chat", None)
        chat = factory(client) if callable(factory) else None
        if chat is not None:
            request_kwargs["chat"] = chat
        completed = False
        def late_response(done: Any) -> None:
            if not done.cancelled() and done.exception() is None:
                observer.observe(done.result())
            observer.fallback(immediate=True, delete_after_seconds=request.delete_after_seconds)
        try:
            with observe_cleanup_frames(observer.observe):
                with client_timeout_scope(client, CHAT_GENERATION_TIMEOUT_SECONDS), model_policy_scope("query", allow_fallback=True):
                    response = await await_before_deadline(
                        client.generate_content(**request_kwargs), timeout=CHAT_GENERATION_TIMEOUT_SECONDS,
                        on_late=late_response,
                    )
                observer.observe(response)
                if self._dependencies.account_key_provider() == owner:
                    scheduled = await self._finalize_one_shot(response, request, expected_account=owner)
                else:
                    scheduled = CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=self._response_chat_id(response),
                                                   source=request.cleanup_source)
                if self._dependencies.account_key_provider() != owner:
                    scheduled = CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=self._response_chat_id(response),
                                                   source=request.cleanup_source)
                if self._dependencies.account_key_provider() == owner and isinstance(scheduled, CleanupObservation) and scheduled.state in {CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE}:
                    observer.failed.add(self._response_chat_id(response) or "")
                completed = True
        finally:
            if chat is not None:
                observer.observe(chat)
            if not completed or observer.failed:
                observer.fallback(immediate=True, delete_after_seconds=request.delete_after_seconds)
        if observer.failed:
            raise RuntimeError("Conversation cleanup could not be persisted.")
        remote_chat_id, cleanup = self._scheduled_cleanup(
            scheduled,
            fallback_chat_id=self._response_chat_id(response),
            retain_chat=request.retain_chat,
            source=request.cleanup_source,
        )
        return self._reply_result(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                lifecycle=ConversationLifecycleMetadata.stateless(
                    upstream_chat_id=remote_chat_id,
                    retain_chat=request.retain_chat,
                    delete_after_seconds=request.delete_after_seconds,
                    cleanup=cleanup,
                ),
                temporary=request.temporary,
                remote_chat_id=remote_chat_id,
                response=response,
            )
        )

    async def generate_stream(
        self,
        request: ChatRequest,
    ) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(request.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        client = await self._prepare_client()
        owner = self._dependencies.account_key_provider()
        if not owner:
            return self._account_failure()
        request_kwargs = self._one_shot_kwargs(
            request,
            effective_model=effective_model,
        )
        responses: list[Any] = []
        collector = StreamTextAccumulator()
        known_responses: dict[str, Any] = {}
        observer = _FrameCleanup(self, retain_chat=request.retain_chat,
                                 delay=max(DEFAULT_SESSION_MAX_AGE_SECONDS, request.delete_after_seconds or 0),
                                 source=request.cleanup_source, owner=owner)
        factory = getattr(type(client), "create_cleanup_chat", None)
        chat = factory(client) if callable(factory) else None
        if chat is not None:
            request_kwargs["chat"] = chat
        completed = False
        async def collect_stream() -> None:
            async for response in client.generate_content_stream(**request_kwargs):
                responses.append(response)
                observer.observe(response)
                cid = self._response_chat_id(response)
                if cid:
                    known_responses[cid] = response
                collector.consume(response)

        def late_stream(_done: Any) -> None:
            observer.fallback(immediate=True, delete_after_seconds=request.delete_after_seconds)

        try:
            with observe_cleanup_frames(observer.observe):
                with client_timeout_scope(client, CHAT_GENERATION_TIMEOUT_SECONDS), model_policy_scope("query", allow_fallback=True):
                    await await_before_deadline(collect_stream(), timeout=CHAT_GENERATION_TIMEOUT_SECONDS,
                                                on_late=late_stream)
                final_response = responses[-1] if responses else None
                remote_chat_id = None
                cleanup = CleanupObservation()
                if final_response is not None:
                    cleanup_response = final_response
                    if not self._response_chat_id(cleanup_response) and known_responses:
                        cleanup_response = next(reversed(known_responses.values()))
                    if self._dependencies.account_key_provider() == owner:
                        scheduled = await self._finalize_one_shot(cleanup_response, request, expected_account=owner)
                    else:
                        scheduled = CleanupObservation(state=CleanupState.FAILED,
                                                       upstream_chat_id=self._response_chat_id(cleanup_response),
                                                       source=request.cleanup_source)
                    if self._dependencies.account_key_provider() != owner:
                        scheduled = CleanupObservation(state=CleanupState.FAILED,
                                                       upstream_chat_id=self._response_chat_id(cleanup_response),
                                                       source=request.cleanup_source)
                    remote_chat_id, cleanup = self._scheduled_cleanup(
                        scheduled, fallback_chat_id=self._response_chat_id(cleanup_response),
                        retain_chat=request.retain_chat, source=request.cleanup_source,
                    )
                    if (self._dependencies.account_key_provider() == owner and
                            cleanup.state in {CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE}):
                        observer.failed.add(remote_chat_id or "")
                completed = final_response is not None
        finally:
            if chat is not None:
                observer.observe(chat)
            if not completed or observer.failed:
                observer.fallback(immediate=True, delete_after_seconds=request.delete_after_seconds)
        if observer.failed:
            raise RuntimeError("Conversation cleanup could not be persisted.")
        if final_response is None:
            observed_id = self._response_chat_id(chat) if chat is not None else None
            return DomainResult.failure(
                DomainErrorCode.UPSTREAM_CHANGED,
                "Gemini returned no response frames; completion and the effective model could not be verified.",
                requested_backend=request.model,
                verification_status="upstream_response_missing",
                details={"service": "chat", "lifecycle": ConversationLifecycleMetadata.stateless(
                    upstream_chat_id=observed_id, retain_chat=request.retain_chat,
                    delete_after_seconds=request.delete_after_seconds,
                    cleanup=cleanup_observation_for_policy(observed_id, retain_chat=request.retain_chat,
                                                          source=request.cleanup_source),
                )},
            )
        return self._reply_result(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                lifecycle=ConversationLifecycleMetadata.stateless(
                    upstream_chat_id=remote_chat_id,
                    retain_chat=request.retain_chat,
                    delete_after_seconds=request.delete_after_seconds,
                    cleanup=cleanup,
                ),
                temporary=request.temporary,
                stream=collector.metadata,
                remote_chat_id=remote_chat_id,
                response=final_response,
                responses=tuple(responses),
                stream_text=collector.text,
            )
        )

    async def start_session(
        self,
        request: StartSessionRequest,
    ) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(request.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        client = await self._prepare_client()
        owner = self._dependencies.account_key_provider()
        if not owner:
            return self._account_failure()
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
        state.account_key = owner
        return self._reply_result(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                lifecycle=self._session_lifecycle(state),
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
        async with state._policy_lock:
            if state.lifecycle_state is not SessionLifecycleState.ACTIVE:
                return DomainResult.failure(DomainErrorCode.SESSION_NOT_FOUND, "The session no longer exists.")
            return await self._send_session_locked(request, state)

    async def _send_session_locked(
        self, request: SessionMessageRequest, state: SessionData,
    ) -> DomainResult[ChatOperationData]:
        if not state.account_key or self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        if request.recover_only:
            return await self._recover_session_pending(request, state)
        if state.pending_turn is not None:
            return self._session_pending_failure()
        policy, failure = self._apply_session_policy(request, state)
        if failure is not None:
            return failure
        prepared_client = await self._prepare_client() if request.prepare_client else None

        if self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        request_kwargs = self._session_request_kwargs(request, state)
        observer = _FrameCleanup(self, retain_chat=state.retain_chat,
                                 delay=(state.delete_after_seconds if state.retain_chat else
                                        max(DEFAULT_SESSION_MAX_AGE_SECONDS, state.delete_after_seconds or 0)),
                                 source=request.cleanup_source, owner=state.account_key, reschedule_pending=True)
        prior_metadata = tuple(getattr(state.session, "metadata", ()) or ())
        def observe_session(value: Any) -> None:
            cid = self._response_chat_id(value)
            if cid:
                if self._remember_session_turn(state, value, cid, prior_metadata):
                    state.unverified_chat_ids.add(cid)
            observer.observe(value)

        client = getattr(state.session, "geminiclient", None) or prepared_client or self._dependencies.client_provider()
        deadline = asyncio.get_running_loop().time() + CHAT_GENERATION_TIMEOUT_SECONDS
        try:
            sent = await self._send_session_request(
                self._dependencies.send_session_message, request.session_id, request_kwargs,
                client=client, observe=observe_session, deadline=deadline,
            )
        except BaseException as error:
            observe_session(state.session)
            if observer.failed:
                observer.fallback(immediate=False)
            if self._ambiguous_session_error(error):
                self._protect_ambiguous_session_turn(state, prior_metadata)
                if isinstance(error, asyncio.CancelledError):
                    raise
                return await self._recover_session_pending(request, state, deadline=deadline)
            raise
        if observer.failed:
            raise RuntimeError("Conversation cleanup could not be persisted.")
        if self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        if not sent.ok or sent.session is None:
            return self._copy_failure(sent)
        state = sent.session
        response = sent.response
        if not self._session_reply_matches_pending(state, response):
            return await self._recover_session_pending(request, state, deadline=deadline)
        if not self._session_reply_has_content(response):
            observe_session(response if self._response_chat_id(response) else state.session)
            self._protect_ambiguous_session_turn(state, prior_metadata)
            if observer.failed:
                raise RuntimeError("Conversation cleanup could not be persisted.")
            return await self._recover_session_pending(request, state, deadline=deadline)
        completed_cid = self._response_chat_id(response)
        if completed_cid:
            state.unverified_chat_ids.discard(completed_cid)
            state.pending_turn = None
            if completed_cid in observer.registered:
                policy = self._completed_session_policy(request, state, completed_cid)
        remote_chat_id, cleanup = self._session_cleanup_after_send(request, state, response, policy)
        return self._session_reply_result(
            request,
            state,
            response=response,
            remote_chat_id=remote_chat_id,
            cleanup=cleanup,
            request_id=sent.meta.request_id,
            verification_status=sent.meta.verification_status,
        )

    async def send_session_stream(
        self,
        request: SessionMessageRequest,
    ) -> DomainResult[ChatOperationData]:
        lookup = self._dependencies.lookup_session(request.session_id)
        if not lookup.ok or lookup.session is None:
            return self._copy_failure(lookup)
        state = lookup.session
        async with state._policy_lock:
            if state.lifecycle_state is not SessionLifecycleState.ACTIVE:
                return DomainResult.failure(DomainErrorCode.SESSION_NOT_FOUND, "The session no longer exists.")
            return await self._send_session_stream_locked(request, state)

    async def _send_session_stream_locked(
        self, request: SessionMessageRequest, state: SessionData,
    ) -> DomainResult[ChatOperationData]:
        if not state.account_key or self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        if request.recover_only:
            return await self._recover_session_pending(request, state)
        if state.pending_turn is not None:
            return self._session_pending_failure()
        policy, failure = self._apply_session_policy(request, state)
        if failure is not None:
            return failure
        prepared_client = await self._prepare_client() if request.prepare_client else None

        if self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        request_kwargs = self._session_request_kwargs(request, state)
        observer = _FrameCleanup(self, retain_chat=state.retain_chat,
                                 delay=(state.delete_after_seconds if state.retain_chat else
                                        max(DEFAULT_SESSION_MAX_AGE_SECONDS, state.delete_after_seconds or 0)),
                                 source=request.cleanup_source, owner=state.account_key, reschedule_pending=True)
        prior_metadata = tuple(getattr(state.session, "metadata", ()) or ())
        def observe_session(value: Any) -> None:
            cid = self._response_chat_id(value)
            if cid:
                if self._remember_session_turn(state, value, cid, prior_metadata):
                    state.unverified_chat_ids.add(cid)
            observer.observe(value)

        client = getattr(state.session, "geminiclient", None) or prepared_client or self._dependencies.client_provider()
        deadline = asyncio.get_running_loop().time() + CHAT_GENERATION_TIMEOUT_SECONDS
        try:
            streamed = await self._send_session_request(
                self._dependencies.send_session_message_stream, request.session_id, request_kwargs,
                client=client, observe=observe_session, deadline=deadline,
            )
        except BaseException as error:
            observe_session(state.session)
            if observer.failed:
                observer.fallback(immediate=False)
            if self._ambiguous_session_error(error):
                self._protect_ambiguous_session_turn(state, prior_metadata)
                if isinstance(error, asyncio.CancelledError):
                    raise
                return await self._recover_session_pending(request, state, deadline=deadline)
            raise
        if observer.failed:
            raise RuntimeError("Conversation cleanup could not be persisted.")
        if self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        if not streamed.ok or streamed.session is None:
            return self._copy_failure(streamed)
        state = streamed.session
        responses = tuple(streamed.response or ())
        final_response = responses[-1] if responses else None
        if not self._session_reply_matches_pending(state, final_response):
            return await self._recover_session_pending(request, state, deadline=deadline)
        matching_responses = tuple(response for response in responses if self._session_reply_matches_pending(state, response))
        collector = StreamTextAccumulator()
        for response in matching_responses:
            collector.consume(response)
        if not collector.text.strip() and not any(self._session_reply_has_content(response) for response in matching_responses):
            observe_session(final_response if self._response_chat_id(final_response) else state.session)
            self._protect_ambiguous_session_turn(state, prior_metadata)
            if observer.failed:
                raise RuntimeError("Conversation cleanup could not be persisted.")
            return await self._recover_session_pending(request, state, deadline=deadline)
        completed_cid = self._response_chat_id(final_response)
        if completed_cid:
            state.unverified_chat_ids.discard(completed_cid)
            state.pending_turn = None
            if completed_cid in observer.registered:
                policy = self._completed_session_policy(request, state, completed_cid)
        remote_chat_id, cleanup = self._session_cleanup_after_send(request, state, final_response, policy)
        return self._session_reply_result(
            request,
            state,
            response=final_response,
            responses=responses,
            stream_text=collector.text,
            stream=collector.metadata,
            remote_chat_id=remote_chat_id,
            cleanup=cleanup,
            request_id=streamed.meta.request_id,
            verification_status=streamed.meta.verification_status,
        )

    @staticmethod
    async def _send_session_request(
        send: Callable[..., Awaitable[SessionOperationResult]], session_id: str, arguments: dict[str, Any],
        *, client: Any, observe: Callable[[Any], None], deadline: float,
    ) -> SessionOperationResult:
        from ..thinking_client import strict_session_recovery_scope

        async def send_once() -> SessionOperationResult:
            # Keep identity observation and the SDK lease with the send if
            # its transport needs time to acknowledge cancellation.
            with strict_session_recovery_scope(), observe_cleanup_frames(observe), model_policy_scope("query"), client_timeout_scope(
                client, CHAT_GENERATION_TIMEOUT_SECONDS
            ):
                return await send(session_id, **arguments)

        return cast(SessionOperationResult, await await_before_deadline(
            send_once(), timeout=deadline - asyncio.get_running_loop().time(),
        ))

    @staticmethod
    def _remember_session_turn(state: SessionData, value: Any, cid: str, previous: tuple[Any, ...]) -> bool:
        metadata = getattr(value, "metadata", None)
        values = metadata if isinstance(metadata, (tuple, list)) and metadata and metadata[0] == cid else ()
        rid = values[1] if len(values) > 1 and isinstance(values[1], str) else ""
        rcid = values[2] if len(values) > 2 and isinstance(values[2], str) and values[2] else None
        if len(previous) > 1 and previous[0] == cid and previous[1] and (not rid or rid == previous[1]):
            return False  # Do not recover the preceding reply as the current one.
        if len(previous) > 2 and rid and rid != previous[1] and rcid == previous[2]:
            rcid = None  # SDK metadata updates may retain the preceding candidate ID.
        current = state.pending_turn
        if current is None or not current[0]:
            state.pending_turn = (cid, rid, rcid)
        elif (current[0] == cid and (not current[1] or not rid or current[1] == rid)
              and (not current[2] or not rcid or current[2] == rcid)):
            state.pending_turn = (cid, rid or current[1], rcid or current[2])
        return True

    @staticmethod
    def _ambiguous_session_error(error: BaseException) -> bool:
        from gemini_webapi.exceptions import (
            APIError, GeminiError, ModelInvalidError, TemporarilyBlockedError, UsageLimitExceededError,
        )
        # AuthError, local validation/model-policy exceptions and the explicit
        # rejection classes below are not evidence that a turn was accepted.
        # API/parse errors and EOF, however, may follow an accepted request even
        # when no response identity reached the client.
        return (isinstance(error, (TimeoutError, APIError, GeminiError, asyncio.CancelledError))
                and not isinstance(error, (UsageLimitExceededError, ModelInvalidError, TemporarilyBlockedError)))

    @staticmethod
    def _protect_ambiguous_session_turn(state: SessionData, previous: tuple[Any, ...]) -> None:
        if state.pending_turn is None:
            # The request may have been accepted even without new turn metadata.
            cid = previous[0] if previous and is_valid_remote_chat_id(previous[0]) else ""
            state.pending_turn = (cid, "", None)
            if cid:
                state.unverified_chat_ids.add(cid)

    @staticmethod
    def _session_reply_matches_pending(state: SessionData, response: Any) -> bool:
        """Never clear an observed pending turn for a different returned reply."""
        pending = state.pending_turn
        if pending is None:
            return True
        metadata = getattr(response, "metadata", ())
        if not isinstance(metadata, (tuple, list)) or not metadata or metadata[0] != pending[0]:
            return False
        rid = metadata[1] if len(metadata) > 1 else None
        rcid = getattr(response, "rcid", None) or (metadata[2] if len(metadata) > 2 else None)
        return bool(pending[0] and (not pending[1] or rid == pending[1])
                    and (pending[2] is None or rcid == pending[2]))

    @staticmethod
    def _session_reply_has_content(response: Any) -> bool:
        text = getattr(response, "text", None)
        return (isinstance(text, str) and bool(text.strip())) or bool(extract_response_artifacts(response))

    @staticmethod
    def _session_pending_failure() -> DomainResult[ChatOperationData]:
        return DomainResult.failure(
            DomainErrorCode.TIMED_OUT,
            "The previous session reply is unverified; use recover_only to read the original turn.",
            operation_state=OperationState.TIMED_OUT, verification_status="session_reply_unverified",
        )

    async def _recover_session_pending(
        self, request: SessionMessageRequest, state: SessionData, *, deadline: float | None = None,
    ) -> DomainResult[ChatOperationData]:
        from gemini_webapi.types import Candidate, ModelOutput
        from .web_context_transport import fetch_workflow_turn

        pending = state.pending_turn
        if not state.account_key or self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        # The session service holds this lock until the original SDK call
        # exits, even if the caller already timed out.
        if not pending or not pending[1] or state._send_lock.locked():
            return self._session_pending_failure()
        remaining = deadline - asyncio.get_running_loop().time() if deadline is not None else None
        if remaining is not None and remaining <= 0:
            return self._session_pending_failure()
        cid, rid, rcid = pending
        client = getattr(state.session, "geminiclient", None) or self._dependencies.client_provider()
        try:
            read = fetch_workflow_turn(client, cid, rid, rcid)
            result = await read if remaining is None else await await_before_deadline(read, timeout=remaining)
        except Exception:
            return self._session_pending_failure()
        if self._dependencies.account_key_provider() != state.account_key:
            return self._account_failure()
        if (not isinstance(result, dict) or result.get("chat_id") != cid or result.get("rid") != rid
                or (rcid is not None and result.get("rcid") != rcid) or result.get("completed") is not True
                or result.get("input_required") or not isinstance(result.get("text"), str)
                or not result["text"].strip() or not isinstance(result.get("rcid"), str)):
            return self._session_pending_failure()
        metadata = [cid, rid, result["rcid"]]
        output = ModelOutput(metadata=metadata, candidates=[Candidate(rcid=result["rcid"], text=result["text"])])
        state.session.metadata = metadata
        state.session.last_output = output
        state.upstream_chat_id = cid
        state.pending_turn = None
        state.unverified_chat_ids.discard(cid)
        policy = self._completed_session_policy(request, state, cid)
        remote_chat_id, cleanup = self._session_cleanup_after_send(request, state, output, policy)
        return self._session_reply_result(
            request, state, response=output, stream_text=output.text, remote_chat_id=remote_chat_id, cleanup=cleanup,
            request_id=SessionOperationResult.success(state, output).meta.request_id,
            verification_status="original_session_reply_verified",
        )

    async def _prepare_client(self) -> Any:
        client = self._dependencies.client_provider()
        await self._dependencies.client_initializer()
        await self._dependencies.cleanup_due_remote_chats(client)
        return client

    async def _finalize_one_shot(
        self, response: Any, request: ChatRequest, *, expected_account: str,
    ) -> CleanupObservation | str | None:
        finalize = self._dependencies.finalize_response_cleanup
        kwargs = dict(retain_chat=request.retain_chat, delete_after_seconds=request.delete_after_seconds,
                      source=request.cleanup_source)
        if finalize is not None:
            return await finalize(response, expected_account=expected_account, **kwargs)
        return self._dependencies.schedule_response_cleanup(response, **kwargs)

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
            "timeout": CHAT_GENERATION_TIMEOUT_SECONDS,
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
            "timeout": CHAT_GENERATION_TIMEOUT_SECONDS,
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
    ) -> tuple[str | None, CleanupObservation]:
        retain_chat = state.retain_chat if request.retain_chat is None else request.retain_chat
        delete_after_seconds = request.delete_after_seconds
        if delete_after_seconds is None:
            delete_after_seconds = state.delete_after_seconds
        if delete_after_seconds is None:
            # Active sessions need their upstream conversation until reset or
            # expiry; this finite hold is not an explicit indefinite retention.
            delete_after_seconds = DEFAULT_SESSION_MAX_AGE_SECONDS

        if request.cleanup_strategy in {
            CleanupStrategy.RESPONSE,
            CleanupStrategy.RESPONSE_THEN_SESSION,
        }:
            scheduled = self._dependencies.schedule_response_cleanup(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source=request.cleanup_source,
            )
            remote_chat_id, cleanup = self._scheduled_cleanup(
                scheduled,
                fallback_chat_id=self._response_chat_id(response),
                retain_chat=retain_chat,
                source=request.cleanup_source,
            )
            if remote_chat_id or request.cleanup_strategy == CleanupStrategy.RESPONSE:
                return remote_chat_id, cleanup

        remote_chat_id = self._session_chat_id(state)
        scheduled = self._dependencies.schedule_chat_cleanup(
            remote_chat_id,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=request.cleanup_source,
        )
        return self._scheduled_cleanup(
            scheduled,
            fallback_chat_id=remote_chat_id,
            retain_chat=retain_chat,
            source=request.cleanup_source,
        )

    def _apply_session_policy(
        self, request: SessionMessageRequest, state: SessionData,
    ) -> tuple[tuple[str, CleanupObservation | None] | None, DomainResult[ChatOperationData] | None]:
        """Persist explicit session policy before a due-cleanup pass or upstream send."""
        if request.retain_chat is None and request.delete_after_seconds is None:
            return None, None
        retain = state.retain_chat if request.retain_chat is None else request.retain_chat
        ttl = state.delete_after_seconds if request.delete_after_seconds is None else max(0, request.delete_after_seconds)
        cid = self._session_chat_id(state)
        policy: tuple[str, CleanupObservation | None] | None = None
        if cid:
            effective_ttl = DEFAULT_SESSION_MAX_AGE_SECONDS if ttl is None else ttl
            try:
                reschedule = {"reschedule_pending": True} if request.delete_after_seconds is not None else {}
                observation = self._dependencies.schedule_chat_cleanup(
                    cid, retain_chat=retain, delete_after_seconds=effective_ttl, source=request.cleanup_source,
                    **reschedule,
                )
            except Exception:
                return None, DomainResult.failure(
                    DomainErrorCode.INTERNAL_ERROR, "The session cleanup policy could not be saved; no message was sent.",
                    verification_status="cleanup_policy_not_saved",
                )
            if isinstance(observation, CleanupObservation) and observation.state is CleanupState.FAILED:
                return None, DomainResult.failure(
                    DomainErrorCode.INTERNAL_ERROR, "The session cleanup policy could not be saved; no message was sent.",
                    verification_status="cleanup_policy_not_saved",
                )
            policy = (cid, observation)
        state.retain_chat = retain
        state.delete_after_seconds = ttl
        return policy, None

    def _session_cleanup_after_send(
        self, request: SessionMessageRequest, state: SessionData, response: Any,
        policy: tuple[str, CleanupObservation | None] | None,
    ) -> tuple[str | None, CleanupObservation]:
        response_cid = self._response_chat_id(response)
        actual_cid = response_cid or self._session_chat_id(state)
        if policy is not None and actual_cid == policy[0]:
            return self._scheduled_cleanup(
                policy[1], fallback_chat_id=policy[0], retain_chat=state.retain_chat, source=request.cleanup_source,
            )
        return self._schedule_session_cleanup(request, state, response)

    def _completed_session_policy(
        self, request: SessionMessageRequest, state: SessionData, cid: str,
    ) -> tuple[str, CleanupObservation | None]:
        """Replace the in-flight hold only after the exact session reply completes."""
        delay = state.delete_after_seconds
        if delay is None:
            delay = DEFAULT_SESSION_MAX_AGE_SECONDS
        try:
            observation = self._dependencies.schedule_chat_cleanup(
                cid, retain_chat=state.retain_chat, delete_after_seconds=delay,
                source=request.cleanup_source, reschedule_pending=True,
            )
        except Exception:
            observation = CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        return cid, observation

    def _session_reply_result(
        self,
        request: SessionMessageRequest,
        state: SessionData,
        *,
        response: Any,
        responses: Sequence[Any] = (),
        stream_text: str = "",
        stream: StreamCollectionMetadata | None = None,
        remote_chat_id: str | None,
        cleanup: CleanupObservation,
        request_id: str,
        verification_status: str,
    ) -> DomainResult[ChatOperationData]:
        normalized_model = self._dependencies.normalize_model(state.model)
        effective_model = self._dependencies.resolve_model(normalized_model)
        temporary = state.temporary if request.temporary is None else request.temporary
        return self._reply_result(
            ChatOperationData(
                requested_model=state.model,
                normalized_model=normalized_model,
                effective_model=effective_model,
                lifecycle=self._session_lifecycle(
                    state,
                    retain_chat=(state.retain_chat if request.retain_chat is None else request.retain_chat),
                    delete_after_seconds=(
                        state.delete_after_seconds
                        if request.delete_after_seconds is None
                        else request.delete_after_seconds
                    ),
                    cleanup=cleanup,
                ),
                session_id=state.session_id,
                session_state=state,
                temporary=temporary,
                stream=stream,
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
        cid = getattr(state.session, "cid", None)
        return cid if is_valid_remote_chat_id(cid) else state.upstream_chat_id

    @staticmethod
    def _response_chat_id(response: Any) -> str | None:
        cid = getattr(response, "cid", None)
        if is_valid_remote_chat_id(cid):
            return cid
        metadata = getattr(response, "metadata", None)
        if isinstance(metadata, list) and metadata:
            candidate = metadata[0]
            if is_valid_remote_chat_id(candidate):
                return candidate
        return None

    @staticmethod
    def _scheduled_cleanup(
        scheduled: CleanupObservation | str | None,
        *,
        fallback_chat_id: str | None,
        retain_chat: bool,
        source: str,
    ) -> tuple[str | None, CleanupObservation]:
        if isinstance(scheduled, CleanupObservation):
            remote_chat_id = scheduled.upstream_chat_id or fallback_chat_id
            return remote_chat_id, scheduled
        embedded = getattr(scheduled, "cleanup_observation", None)
        if isinstance(embedded, CleanupObservation):
            remote_chat_id = embedded.upstream_chat_id or fallback_chat_id
            return remote_chat_id, embedded
        remote_chat_id = scheduled if is_valid_remote_chat_id(scheduled) else fallback_chat_id
        return remote_chat_id, cleanup_observation_for_policy(
            remote_chat_id,
            retain_chat=retain_chat,
            source=source,
        )

    @staticmethod
    def _session_lifecycle(
        state: SessionData,
        *,
        retain_chat: bool | None = None,
        delete_after_seconds: int | None = None,
        cleanup: CleanupObservation | None = None,
    ) -> ConversationLifecycleMetadata:
        return ConversationLifecycleMetadata(
            session_id=state.session_id,
            upstream_chat_id=ChatService._session_chat_id(state),
            session_state=SessionLifecycleState.ACTIVE,
            retain_chat=state.retain_chat if retain_chat is None else retain_chat,
            delete_after_seconds=(state.delete_after_seconds if delete_after_seconds is None else delete_after_seconds),
            cleanup=cleanup or CleanupObservation(),
        )

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
    def _reply_result(
        data: ChatOperationData,
        *,
        request_id: str | None = None,
        verification_status: str = "upstream_response_received",
    ) -> DomainResult[ChatOperationData]:
        selection = model_selection_from_response(data.response)
        warnings: list[DomainWarning] = []
        details: dict[str, Any] = {"service": "chat", "lifecycle": data.lifecycle}
        if selection is not None:
            data = replace(data, effective_model=selection.effective_model, model_selection=selection)
            details["model_selection"] = selection
            if selection.fallback_reason:
                warnings.append(DomainWarning(code="model_fallback", message="Verified Flash quota exhaustion selected Flash-Lite for this new request."))
            if not selection.thinking_selectable:
                warnings.append(DomainWarning(code="thinking_unavailable", message="Thinking strength is fixed in quota fallback; the requested strength was not applied."))
        final_text = getattr(data.response, "text", None) or data.stream_text
        if is_generation_failure_reply(final_text) and not extract_response_artifacts(data.response):
            return DomainResult.failure(
                DomainErrorCode.UPSTREAM_REJECTED,
                "Gemini returned a generation failure notice.",
                data=replace(data, session_state=None),
                retryable=True,
                request_id=request_id,
                warnings=warnings,
                requested_backend=data.requested_model,
                effective_backend=data.effective_model,
                verification_status="upstream_generation_failure_observed",
                details=details,
            )
        return DomainResult.success(
            data,
            warnings=tuple(warnings),
            request_id=request_id,
            requested_backend=data.requested_model,
            effective_backend=data.effective_model,
            verification_status=verification_status,
            details=details,
        )
