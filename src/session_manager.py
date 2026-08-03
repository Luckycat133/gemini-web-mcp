"""
会话管理器 - 负责本地会话的存储、检索和清理
"""

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .constants import DEFAULT_CHAT_RETENTION_SECONDS
from .domain import (
    CleanupObservation,
    ConversationLifecycleMetadata,
    DomainError,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    ResultMeta,
    SessionLifecycleState,
)

logger = logging.getLogger(__name__)

SESSION_NOT_FOUND = DomainErrorCode.SESSION_NOT_FOUND.value


@dataclass
class SessionState:
    """Typed local handle and mutable lifecycle state for one upstream session."""

    session: Any = field(metadata={"domain_exclude": True})
    session_id: str = ""
    model: str = "flash"
    thinking_level: str = "standard"
    learning_mode: str | None = None
    temporary: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    upstream_chat_id: str | None = None
    retain_chat: bool = False
    delete_after_seconds: int | None = None
    lifecycle_state: SessionLifecycleState = SessionLifecycleState.ACTIVE
    _send_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        repr=False,
        compare=False,
        metadata={"domain_exclude": True},
    )


# Public target names plus the existing import name used by older callers.
SessionHandle = SessionState
SessionData = SessionState


@dataclass(frozen=True)
class SessionOperationData:
    """Public session state plus an adapter-only upstream response."""

    state: SessionData | None = None
    response: Any = field(default=None, metadata={"domain_exclude": True})


class SessionOperationResult(DomainResult[SessionOperationData]):
    """Domain result specialized for shared session lifecycle operations.

    ``session`` and ``response`` remain as compatibility properties for callers
    written against the pre-P0.3 contract. New callers can consume the generic
    ``data``, ``error`` and ``meta`` fields.
    """

    @classmethod
    def success(  # type: ignore[override]  # compatibility factory predates DomainResult
        cls,
        session: SessionData | None = None,
        response: Any = None,
        *,
        verification_status: str = "not_applicable",
        cleanup: CleanupObservation | None = None,
        lifecycle_state: SessionLifecycleState | None = None,
        operation_state: OperationState = OperationState.COMPLETED,
        warnings: tuple[DomainWarning, ...] = (),
        request_id: str | None = None,
    ) -> "SessionOperationResult":
        observed_state = lifecycle_state or (
            session.lifecycle_state if session is not None else SessionLifecycleState.STATELESS
        )
        lifecycle = ConversationLifecycleMetadata(
            session_id=session.session_id if session is not None else None,
            upstream_chat_id=(
                getattr(session.session, "cid", None) or session.upstream_chat_id if session is not None else None
            ),
            session_state=observed_state,
            retain_chat=session.retain_chat if session is not None else False,
            delete_after_seconds=(session.delete_after_seconds if session is not None else None),
            cleanup=cleanup or CleanupObservation(),
        )
        return cls(
            ok=True,
            data=SessionOperationData(state=session, response=response),
            error=None,
            warnings=warnings,
            meta=ResultMeta.create(
                operation_state,
                request_id=request_id,
                requested_backend=session.model if session is not None else None,
                verification_status=verification_status,
                details={"lifecycle": lifecycle},
            ),
        )

    @classmethod
    def not_found(cls, session_id: str | None = None) -> "SessionOperationResult":
        message = "The requested session does not exist."
        lifecycle = ConversationLifecycleMetadata(
            session_id=session_id,
            session_state=SessionLifecycleState.ABSENT,
        )
        return cls(
            ok=False,
            data=None,
            error=DomainError(
                code=DomainErrorCode.SESSION_NOT_FOUND,
                message=message,
                retryable=False,
                suggested_action=("Create a session with gemini_start_chat and use the returned ID."),
            ),
            warnings=(),
            meta=ResultMeta.create(
                OperationState.FAILED,
                verification_status="local_state_absent",
                details={"lifecycle": lifecycle},
            ),
        )

    @property
    def session(self) -> SessionData | None:
        return self.data.state if self.data is not None else None

    @property
    def response(self) -> Any:
        return self.data.response if self.data is not None else None


class SessionService:
    """Shared, thread-safe lifecycle service for primary and compact adapters."""

    def __init__(
        self,
        max_age: int = DEFAULT_CHAT_RETENTION_SECONDS,
        id_factory: Callable[[], str] | None = None,
    ):
        self._sessions: dict[str, SessionData] = {}
        self._expired_sessions: list[SessionData] = []
        self._lock = threading.Lock()
        self._max_age = max_age
        self._id_factory = id_factory or (lambda: f"sess_{uuid.uuid4().hex}")

    def create_session(
        self,
        session: Any,
        model: str = "flash",
        thinking_level: str = "standard",
        learning_mode: str | None = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
    ) -> SessionOperationResult:
        """Create a session with one opaque collision-resistant local ID."""
        with self._lock:
            self._clean_expired_sessions()
            session_id = self._next_session_id()
            data = self._build_session_data(
                session_id,
                session,
                model,
                thinking_level,
                learning_mode,
                temporary,
                retain_chat,
                delete_after_seconds,
            )
            self._sessions[session_id] = data
        return SessionOperationResult.success(
            data,
            verification_status="local_state_created",
        )

    def store_session(
        self,
        session_id: str,
        session: Any,
        model: str = "flash",
        thinking_level: str = "standard",
        learning_mode: str | None = None,
        temporary: bool = False,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
    ) -> SessionData:
        """存储会话"""
        with self._lock:
            data = self._build_session_data(
                session_id,
                session,
                model,
                thinking_level,
                learning_mode,
                temporary,
                retain_chat,
                delete_after_seconds,
            )
            self._sessions[session_id] = data
            return data

    def lookup_session(self, session_id: str) -> SessionOperationResult:
        """Return an explicit result instead of overloading a missing value."""
        data = self.get_session(session_id)
        if data is None:
            return SessionOperationResult.not_found(session_id)
        return SessionOperationResult.success(
            data,
            verification_status="local_state_found",
        )

    def get_session(self, session_id: str) -> SessionData | None:
        """获取存储的会话"""
        with self._lock:
            self._clean_expired_sessions()
            return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> None:
        """移除会话"""
        self.reset_one(session_id)

    def pop_session(self, session_id: str) -> SessionData | None:
        """移除并返回会话数据。"""
        return self.reset_one(session_id).session

    async def send_message(self, session_id: str, **request_kwargs: Any) -> SessionOperationResult:
        """Serialize sends per session and return explicit not-found state."""
        lookup = self.lookup_session(session_id)
        if not lookup.ok or lookup.session is None:
            return lookup
        data = lookup.session

        async with data._send_lock:
            with self._lock:
                self._clean_expired_sessions()
                if self._sessions.get(session_id) is not data:
                    return SessionOperationResult.not_found(session_id)

            response = await data.session.send_message(**request_kwargs)
            with self._lock:
                if self._sessions.get(session_id) is data:
                    data.updated_at = time.time()
                    data.upstream_chat_id = getattr(data.session, "cid", data.upstream_chat_id)
            return SessionOperationResult.success(
                data,
                response,
                verification_status="upstream_response_received",
            )

    async def send_message_stream(
        self,
        session_id: str,
        **request_kwargs: Any,
    ) -> SessionOperationResult:
        """Collect one upstream stream while holding the same per-session send lock."""
        lookup = self.lookup_session(session_id)
        if not lookup.ok or lookup.session is None:
            return lookup
        data = lookup.session

        async with data._send_lock:
            with self._lock:
                self._clean_expired_sessions()
                if self._sessions.get(session_id) is not data:
                    return SessionOperationResult.not_found(session_id)

            responses = []
            async for response in data.session.send_message_stream(**request_kwargs):
                responses.append(response)
            with self._lock:
                if self._sessions.get(session_id) is data:
                    data.updated_at = time.time()
                    data.upstream_chat_id = getattr(data.session, "cid", data.upstream_chat_id)
            return SessionOperationResult.success(
                data,
                responses,
                verification_status="upstream_response_received",
            )

    async def reset_one_async(self, session_id: str) -> SessionOperationResult:
        """Wait for an in-flight send before detaching exactly one session."""
        lookup = self.lookup_session(session_id)
        if not lookup.ok or lookup.session is None:
            return lookup
        data = lookup.session

        async with data._send_lock:
            with self._lock:
                self._clean_expired_sessions()
                if self._sessions.get(session_id) is not data:
                    return SessionOperationResult.not_found(session_id)
                self._sessions.pop(session_id)
                data.lifecycle_state = SessionLifecycleState.REMOVED
            return SessionOperationResult.success(
                data,
                verification_status="local_state_removed",
            )

    def reset_one(self, session_id: str) -> SessionOperationResult:
        """Reset exactly one session; an unknown ID never affects other state."""
        with self._lock:
            self._clean_expired_sessions()
            data = self._sessions.pop(session_id, None)
        if data is None:
            return SessionOperationResult.not_found(session_id)
        data.lifecycle_state = SessionLifecycleState.REMOVED
        return SessionOperationResult.success(
            data,
            verification_status="local_state_removed",
        )

    def reset_all(self) -> list[SessionData]:
        """Explicitly reset every local session and return the detached states."""
        with self._lock:
            self._clean_expired_sessions()
            sessions = list(self._sessions.values())
            self._sessions.clear()
            for data in sessions:
                data.lifecycle_state = SessionLifecycleState.REMOVED
        return sessions

    async def reset_all_async(self) -> list[SessionData]:
        """Detach the invocation snapshot after any in-flight sends finish."""
        with self._lock:
            self._clean_expired_sessions()
            snapshot = list(self._sessions.values())

        removed: list[SessionData] = []
        for data in snapshot:
            async with data._send_lock:
                with self._lock:
                    if self._sessions.get(data.session_id) is not data:
                        continue
                    self._sessions.pop(data.session_id)
                    data.lifecycle_state = SessionLifecycleState.REMOVED
                    removed.append(data)
        return removed

    def list_sessions(self) -> dict[str, SessionData]:
        """获取所有会话"""
        with self._lock:
            self._clean_expired_sessions()
            return dict(self._sessions)

    def clear_sessions(self) -> None:
        """清空所有会话"""
        self.reset_all()

    def cleanup_expired_sessions(self) -> None:
        """清理过期会话。"""
        with self._lock:
            self._clean_expired_sessions()

    def drain_expired_sessions(self) -> list[SessionData]:
        """Return newly expired states once so lifecycle cleanup can be scheduled."""
        with self._lock:
            expired = list(self._expired_sessions)
            self._expired_sessions.clear()
        return expired

    def _clean_expired_sessions(self) -> None:
        """清理过期会话（内部函数，需在锁内调用）"""
        now = time.time()
        expired = [sid for sid, data in self._sessions.items() if now - data.created_at > self._max_age]
        for sid in expired:
            data = self._sessions.pop(sid)
            data.lifecycle_state = SessionLifecycleState.EXPIRED
            self._expired_sessions.append(data)
        if expired:
            logger.info(f"清理了 {len(expired)} 个过期会话")

    def _next_session_id(self) -> str:
        """Generate an ID while the service lock protects the collision check."""
        while True:
            session_id = self._id_factory()
            if session_id and session_id not in self._sessions:
                return session_id

    @staticmethod
    def _build_session_data(
        session_id: str,
        session: Any,
        model: str,
        thinking_level: str,
        learning_mode: str | None,
        temporary: bool,
        retain_chat: bool,
        delete_after_seconds: int | None,
    ) -> SessionData:
        now = time.time()
        return SessionData(
            session=session,
            session_id=session_id,
            model=model,
            thinking_level=thinking_level,
            learning_mode=learning_mode,
            temporary=temporary,
            created_at=now,
            updated_at=now,
            upstream_chat_id=getattr(session, "cid", None),
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
        )


# Backward-compatible import name; SessionService is the single implementation.
SessionManager = SessionService
