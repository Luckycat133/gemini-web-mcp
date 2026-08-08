"""Conversation lifecycle orchestration below both MCP adapters."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from ..domain import (
    CleanupObservation,
    CleanupState,
    ConversationLifecycleMetadata,
    DomainResult,
    DomainWarning,
    OperationState,
    SessionLifecycleState,
    cleanup_observation_for_policy,
    is_valid_remote_chat_id,
    new_diagnostic_id,
)
from ..remote_chat_cleanup_manager import (
    RemoteChatCleanupManager,
    extract_remote_chat_id,
)
from ..session_manager import (
    SessionData,
    SessionOperationResult,
    SessionService,
)

logger = logging.getLogger(__name__)

DeleteCallback = Callable[[str], Awaitable[bool | CleanupObservation]]


@dataclass(frozen=True)
class LifecycleResetAllData:
    """Observable result of an explicit reset-all request."""

    removed_count: int
    cleanup_failure_count: int
    conversations: tuple[ConversationLifecycleMetadata, ...]


class ConversationLifecycleService:
    """Own local sessions, upstream IDs, retention, and cleanup outcomes.

    Providers keep the historical module-level monkeypatch seams working while
    production routes both MCP adapters through this single orchestrator.
    """

    def __init__(
        self,
        session_provider: Callable[[], Any],
        cleanup_provider: Callable[[], Any],
    ) -> None:
        self._session_provider = session_provider
        self._cleanup_provider = cleanup_provider

    def create_session(self, *args: Any, **kwargs: Any) -> SessionOperationResult:
        result = self._sessions().create_session(*args, **kwargs)
        self._schedule_expired_sessions()
        return result

    def store_session(self, *args: Any, **kwargs: Any) -> SessionData:
        result = self._sessions().store_session(*args, **kwargs)
        self._schedule_expired_sessions()
        return result

    def get_session(self, session_id: str) -> SessionData | None:
        result = self._sessions().get_session(session_id)
        self._schedule_expired_sessions()
        return result

    def lookup_session(self, session_id: str) -> SessionOperationResult:
        result = self._sessions().lookup_session(session_id)
        self._schedule_expired_sessions()
        return result

    async def send_message(
        self,
        session_id: str,
        **request_kwargs: Any,
    ) -> SessionOperationResult:
        result = await self._sessions().send_message(session_id, **request_kwargs)
        self._schedule_expired_sessions()
        return result

    async def send_message_stream(
        self,
        session_id: str,
        **request_kwargs: Any,
    ) -> SessionOperationResult:
        result = await self._sessions().send_message_stream(
            session_id,
            **request_kwargs,
        )
        self._schedule_expired_sessions()
        return result

    async def reset_one(
        self,
        session_id: str,
        *,
        delete_callback: DeleteCallback,
    ) -> SessionOperationResult:
        result = await self._sessions().reset_one_async(session_id)
        self._schedule_expired_sessions()
        state = result.session
        if not result.ok or state is None:
            return result
        state.lifecycle_state = SessionLifecycleState.REMOVED

        cleanup = await self._cleanup_detached_state(
            state,
            delete_callback=delete_callback,
            source="session_reset",
        )
        operation_state, warnings, verification_status = self._cleanup_summary(
            cleanup,
            success_status="local_state_removed_cleanup_observed",
        )
        return SessionOperationResult.success(
            state,
            verification_status=verification_status,
            cleanup=cleanup,
            lifecycle_state=SessionLifecycleState.REMOVED,
            operation_state=operation_state,
            warnings=warnings,
            request_id=result.meta.request_id,
        )

    async def reset_all(
        self,
        *,
        delete_callback: DeleteCallback,
    ) -> DomainResult[LifecycleResetAllData]:
        sessions = self._sessions()
        if isinstance(sessions, SessionService):
            detached = await sessions.reset_all_async()
        else:
            reset_all_async = getattr(sessions, "reset_all_async", None)
            if callable(reset_all_async):
                detached = await reset_all_async()
            else:
                detached = sessions.reset_all()
        self._schedule_expired_sessions()

        lifecycles = []
        warnings = []
        failures = 0
        for state in detached or ():
            cleanup = await self._cleanup_detached_state(
                state,
                delete_callback=delete_callback,
                source="session_reset_all",
            )
            lifecycle = self.metadata_for_session(
                state,
                session_state=SessionLifecycleState.REMOVED,
                cleanup=cleanup,
            )
            lifecycles.append(lifecycle)
            if cleanup.state in {CleanupState.FAILED, CleanupState.INVALID_ID}:
                failures += 1
                warnings.append(self._cleanup_warning(cleanup))

        data = LifecycleResetAllData(
            removed_count=len(lifecycles),
            cleanup_failure_count=failures,
            conversations=tuple(lifecycles),
        )
        return DomainResult.success(
            data,
            operation_state=(OperationState.PARTIAL if failures else OperationState.COMPLETED),
            warnings=tuple(warnings),
            verification_status=(
                "local_state_removed_cleanup_failed" if failures else "local_state_removed_cleanup_observed"
            ),
            details={
                "lifecycle": {
                    "scope": "all",
                    "removed_count": len(lifecycles),
                    "cleanup_failure_count": failures,
                }
            },
        )

    def remove_session(self, session_id: str) -> None:
        sessions = self._sessions()
        if isinstance(sessions, SessionService):
            result = sessions.reset_one(session_id)
            if result.session is not None:
                self._schedule_detached_state(
                    result.session,
                    source="session_remove",
                )
        else:
            sessions.remove_session(session_id)
        self._schedule_expired_sessions()

    def pop_session(self, session_id: str) -> SessionData | None:
        sessions = self._sessions()
        if isinstance(sessions, SessionService):
            result = sessions.reset_one(session_id).session
            if result is not None:
                self._schedule_detached_state(result, source="session_pop")
        else:
            result = sessions.pop_session(session_id)
        self._schedule_expired_sessions()
        return result

    def clear_sessions(self) -> None:
        sessions = self._sessions()
        if isinstance(sessions, SessionService):
            for state in sessions.reset_all():
                self._schedule_detached_state(state, source="session_clear")
        else:
            sessions.clear_sessions()
        self._schedule_expired_sessions()

    def cleanup_expired_sessions(self) -> None:
        self._sessions().cleanup_expired_sessions()
        self._schedule_expired_sessions()

    def list_sessions(self) -> dict[str, SessionData]:
        result = self._sessions().list_sessions()
        self._schedule_expired_sessions()
        return result

    def schedule_cleanup_from_response(
        self,
        response: Any,
        *,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        source: str = "",
    ) -> CleanupObservation:
        cleanup = self._cleanup()
        if isinstance(cleanup, RemoteChatCleanupManager):
            return cleanup.schedule_cleanup_result_from_response(
                response,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source=source,
            )
        cid = cleanup.schedule_cleanup_from_response(
            response,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=source,
        )
        return cleanup_observation_for_policy(
            cid,
            retain_chat=retain_chat,
            source=source,
        )

    def schedule_cleanup(
        self,
        cid: str | None,
        *,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        source: str = "",
    ) -> CleanupObservation:
        cleanup = self._cleanup()
        if isinstance(cleanup, RemoteChatCleanupManager):
            return cleanup.schedule_cleanup_result(
                cid,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source=source,
            )
        cleanup.schedule_cleanup(
            cid,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=source,
        )
        return cleanup_observation_for_policy(
            cid if is_valid_remote_chat_id(cid) else None,
            retain_chat=retain_chat,
            source=source,
        )

    async def delete_chat_result(
        self,
        cid: str | None,
        *,
        client: Any = None,
    ) -> CleanupObservation:
        if cid is None:
            return CleanupObservation()
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(state=CleanupState.INVALID_ID)
        cleanup = self._cleanup()
        if isinstance(cleanup, RemoteChatCleanupManager):
            return await cleanup.delete_chat_result(cid, client=client)
        deleted = await cleanup.delete_chat(cid, client=client)
        return CleanupObservation(
            state=CleanupState.COMPLETED if deleted else CleanupState.FAILED,
            upstream_chat_id=cid,
            attempts=1,
        )

    async def cleanup_due_chats(self, *, client: Any = None) -> int:
        return await self._cleanup().cleanup_due_chats(client=client)

    def observe_cleanup(self, cid: str | None) -> CleanupObservation | None:
        cleanup = self._cleanup()
        if not isinstance(cleanup, RemoteChatCleanupManager):
            return None
        return cleanup.get_cleanup_observation(cid)

    @staticmethod
    def metadata_for_session(
        state: SessionData,
        *,
        session_state: SessionLifecycleState | None = None,
        cleanup: CleanupObservation | None = None,
    ) -> ConversationLifecycleMetadata:
        return ConversationLifecycleMetadata(
            session_id=state.session_id,
            upstream_chat_id=(extract_remote_chat_id(state.session) or state.upstream_chat_id),
            session_state=session_state or state.lifecycle_state,
            retain_chat=state.retain_chat,
            delete_after_seconds=state.delete_after_seconds,
            cleanup=cleanup or CleanupObservation(),
        )

    async def _cleanup_detached_state(
        self,
        state: SessionData,
        *,
        delete_callback: DeleteCallback,
        source: str,
    ) -> CleanupObservation:
        cid = extract_remote_chat_id(state.session) or state.upstream_chat_id
        if cid is None:
            return CleanupObservation(source=source)
        if state.retain_chat:
            return self.schedule_cleanup(
                cid,
                retain_chat=True,
                delete_after_seconds=state.delete_after_seconds,
                source=source,
            )
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(
                state=CleanupState.INVALID_ID,
                source=source,
            )

        try:
            deleted = await delete_callback(cid)
        except Exception as error:  # noqa: BLE001 - preserve cleanup evidence from adapter failures
            cleanup = self._cleanup()
            if isinstance(cleanup, RemoteChatCleanupManager):
                return cleanup.record_cleanup_failure(
                    cid,
                    error,
                    source=source,
                )
            diagnostic_id = new_diagnostic_id()
            logger.warning(
                "Remote cleanup callback failed cid=%s diagnostic_id=%s error_type=%s error=%r",
                cid,
                diagnostic_id,
                type(error).__name__,
                error,
            )
            return CleanupObservation(
                state=CleanupState.FAILED,
                upstream_chat_id=cid,
                attempts=1,
                diagnostic_id=diagnostic_id,
                source=source,
            )
        if isinstance(deleted, CleanupObservation):
            return deleted
        observed = self.observe_cleanup(cid)
        if observed is not None:
            return observed
        if deleted:
            return CleanupObservation(
                state=CleanupState.COMPLETED,
                upstream_chat_id=cid,
                attempts=1,
                source=source,
            )

        diagnostic_id = new_diagnostic_id()
        logger.warning(
            "Remote cleanup failed without manager evidence cid=%s diagnostic_id=%s",
            cid,
            diagnostic_id,
        )
        return CleanupObservation(
            state=CleanupState.FAILED,
            upstream_chat_id=cid,
            attempts=1,
            diagnostic_id=diagnostic_id,
            source=source,
        )

    def _schedule_expired_sessions(self) -> None:
        sessions = self._sessions()
        if not isinstance(sessions, SessionService):
            return
        for state in sessions.drain_expired_sessions():
            cid = extract_remote_chat_id(state.session) or state.upstream_chat_id
            self.schedule_cleanup(
                cid,
                retain_chat=state.retain_chat,
                delete_after_seconds=state.delete_after_seconds,
                source="session_expired",
            )

    def _schedule_detached_state(
        self,
        state: SessionData,
        *,
        source: str,
    ) -> CleanupObservation:
        cid = extract_remote_chat_id(state.session) or state.upstream_chat_id
        return self.schedule_cleanup(
            cid,
            retain_chat=state.retain_chat,
            delete_after_seconds=state.delete_after_seconds,
            source=source,
        )

    @staticmethod
    def _cleanup_summary(
        cleanup: CleanupObservation,
        *,
        success_status: str,
    ) -> tuple[OperationState, tuple[DomainWarning, ...], str]:
        if cleanup.state not in {CleanupState.FAILED, CleanupState.INVALID_ID}:
            return OperationState.COMPLETED, (), success_status
        return (
            OperationState.PARTIAL,
            (ConversationLifecycleService._cleanup_warning(cleanup),),
            "local_state_removed_cleanup_failed",
        )

    @staticmethod
    def _cleanup_warning(cleanup: CleanupObservation) -> DomainWarning:
        diagnostic_suffix = f" Diagnostic ID: {cleanup.diagnostic_id}." if cleanup.diagnostic_id else ""
        return DomainWarning(
            code="REMOTE_CHAT_CLEANUP_FAILED",
            message=(
                f"The local session was removed, but its upstream chat cleanup did not complete.{diagnostic_suffix}"
            ),
            suggested_action="Retry cleanup or inspect the diagnostic ID.",
        )

    def _sessions(self) -> SessionService:
        return cast(SessionService, self._session_provider())

    def _cleanup(self) -> RemoteChatCleanupManager:
        return cast(RemoteChatCleanupManager, self._cleanup_provider())
