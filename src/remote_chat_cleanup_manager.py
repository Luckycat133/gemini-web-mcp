"""
远程聊天清理管理器 - 负责远端 Gemini chat 的自动删除调度和执行
"""

import asyncio
import inspect
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from .constants import DEFAULT_CHAT_RETENTION_SECONDS
from .infrastructure.state_store import RETENTION_SECONDS, CleanupJobRecord, StateStore, authentication_scope
from .domain import (
    CleanupObservation,
    CleanupState,
    is_valid_remote_chat_id,
    new_diagnostic_id,
)

logger = logging.getLogger(__name__)

_PERSISTENT_SOURCES = frozenset({
    "generated_chat", "research", "gemini_ask", "gemini_chat", "gemini_chat_stream",
    "gemini_send_message", "gemini_send_message_stream", "gemini_upload_file", "gemini_analyze_url",
    "gemini_deep_research", "gemini_deep_research:fallback", "gemini_generate_media", "gemini_image",
    "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
    "skill_create", "skill_chat", "skill_chat:session", "skill_session:send",
    "creation:image", "creation:image_edit", "creation:video", "creation:music",
    "creation.recover:image", "creation.recover:image_edit", "creation.recover:video", "creation.recover:music",
    "gemini_generate_media:image", "gemini_generate_media:image_edit", "gemini_generate_media:video", "gemini_generate_media:music",
    "skill_create:image", "skill_create:image_edit", "skill_create:video", "skill_create:music",
    "session_reset", "session_reset_all", "session_remove", "session_pop", "session_clear", "session_expired",
})


@dataclass
class CleanupTask:
    """清理任务数据结构"""

    delete_at: float
    source: str = ""
    attempts: int = 0
    last_diagnostic_id: str | None = None
    authentication_generation: int = 0
    durable_version: int | None = None


def extract_remote_chat_id(obj: Any) -> str | None:
    """从 Gemini response/chat/session 对象中提取远端 chat id。

    注意：src/tools/utils.py 有同名函数，两者实现必须保持一致。
    此处保留本地副本是为了避免 remote_chat_cleanup_manager → tools.utils →
    tools.__init__ → client_wrapper 的循环导入。详见 P1-dedup 决策记录。
    """
    cid = getattr(obj, "cid", None)
    if isinstance(cid, str) and cid.startswith("c_"):
        return cid

    metadata = getattr(obj, "metadata", None)
    if isinstance(metadata, list) and metadata:
        cid = metadata[0]
        if isinstance(cid, str) and cid.startswith("c_"):
            return cid

    return None


class RemoteChatCleanupManager:
    """远程聊天清理管理器 - 线程安全的清理任务调度"""

    def __init__(
        self,
        default_retention_seconds: int = DEFAULT_CHAT_RETENTION_SECONDS,
        client_provider: Callable[[], Any] | None = None,
        retention_provider: Callable[[], int] | None = None,
        state_store: StateStore | None = None,
        lease_seconds: float = 60.0,
    ):
        self._pending_cleanup: dict[str, CleanupTask] = {}
        self._cleanup_observations: dict[str, CleanupObservation] = {}
        self._completed_cleanup: dict[str, CleanupObservation] = {}
        self._inflight_cleanup: dict[str, asyncio.Task[CleanupObservation]] = {}
        self._delayed_cleanup: dict[str, asyncio.Task[None]] = {}
        self._authentication_generation = 0
        self._authentication_context_id = uuid.uuid4().hex
        self._observation_generations: dict[str, int] = {}
        self._cancelled_cleanup: dict[tuple[int, str], CleanupObservation] = {}
        self._lock = threading.Lock()
        self._default_retention = default_retention_seconds
        self._client_provider = client_provider
        self._retention_provider = retention_provider
        self._state_store = state_store
        self._authentication_scope: str | None = None
        self._lease_seconds = max(0.03, lease_seconds)
        self._suspended_cleanup: set[str] = set()

    def bind_authentication_scope(self, scope_id: str | None, *, state_store: StateStore | None = None) -> None:
        """Restore only explicitly registered jobs belonging to these credentials.

        The durable identity is a locally keyed digest, separate from the
        process generation that rejects late results after an account switch.
        No cookie material is retained by this manager.
        """
        with self._lock:
            previous_scope = self._authentication_scope
            store_changed = state_store is not None and state_store is not self._state_store
        if previous_scope is not None and (previous_scope != scope_id or store_changed):
            self.invalidate_authentication_context()
        with self._lock:
            if state_store is not None:
                self._state_store = state_store
            self._authentication_scope = scope_id
            self._suspended_cleanup.clear()
            self._refresh_durable_jobs_locked()
        self.resume_cleanup_jobs()

    def _durable_records_locked(self) -> tuple[CleanupJobRecord, ...]:
        if self._state_store is None or self._authentication_scope is None:
            return ()
        records: list[CleanupJobRecord] = []
        while True:
            page = self._state_store.cleanup_jobs.list(self._authentication_scope, limit=100, offset=len(records))
            records.extend(page)
            if len(page) < 100:
                return tuple(records)

    def _adopt_durable_record_locked(self, record: CleanupJobRecord) -> CleanupObservation:
        state = CleanupState.PENDING if record.state == "running" else CleanupState(record.state)
        observation = CleanupObservation(
            state=state, upstream_chat_id=record.resource_id, attempts=record.attempts,
            diagnostic_id=record.diagnostic_id, source=record.source, delete_at=record.due_at,
            cancellation_reason=(record.error_code or "cancelled_by_request") if state is CleanupState.CANCELLED else None,
        )
        cid = record.resource_id
        previous = self._cleanup_observations.get(cid)
        if previous is not None and previous == observation:
            observation = previous
        if record.state in {"pending", "failed", "running"}:
            due_at = max(record.due_at, record.lease_until or 0) if record.state == "running" else record.due_at
            existing = self._pending_cleanup.get(cid)
            if existing is None or existing.durable_version != record.version:
                self._pending_cleanup[cid] = CleanupTask(
                    delete_at=due_at, source=record.source, attempts=record.attempts,
                    last_diagnostic_id=record.diagnostic_id,
                    authentication_generation=self._authentication_generation, durable_version=record.version,
                )
        else:
            self._pending_cleanup.pop(cid, None)
            self._cancel_delayed_cleanup_locked(cid)
        if state is CleanupState.COMPLETED:
            self._completed_cleanup[cid] = observation
        self._store_observation_locked(cid, observation, self._authentication_generation)
        return observation

    def _refresh_durable_jobs_locked(self) -> None:
        for record in self._durable_records_locked():
            self._adopt_durable_record_locked(record)

    @staticmethod
    def _persistent_source(source: str) -> str:
        # Persist a controlled kind, never a caller-provided prompt or label.
        if source in _PERSISTENT_SOURCES:
            return source
        return "generated_chat"

    def _enqueue_durable_locked(self, cid: str, delete_at: float, source: str) -> CleanupJobRecord:
        assert self._state_store is not None and self._authentication_scope is not None
        now = time.time()
        return self._state_store.cleanup_jobs.upsert(CleanupJobRecord(
            job_id="cleanup_" + uuid.uuid4().hex, scope_id=self._authentication_scope,
            resource_id=cid, state="pending", due_at=delete_at, source=self._persistent_source(source),
            created_at=now, updated_at=now, expires_at=max(now, delete_at) + RETENTION_SECONDS,
        ))

    def _storage_failure_locked(self, cid: str, source: str, *, attempts: int = 0) -> CleanupObservation:
        observation = CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid,
                                         attempts=attempts, source=source, diagnostic_id=new_diagnostic_id())
        self._suspended_cleanup.add(cid)
        self._store_observation_locked(cid, observation, self._authentication_generation)
        return observation

    def _durable_schedule_locked(
        self, cid: str, *, retain_chat: bool, explicit_delay: bool, delete_at: float, source: str,
    ) -> CleanupObservation | None:
        if self._state_store is None:
            return None
        if self._authentication_scope is None:
            return CleanupObservation(state=CleanupState.CANCELLED, upstream_chat_id=cid,
                                      cancellation_reason="authentication_context_unavailable", source=source)
        repository = self._state_store.cleanup_jobs
        record = repository.get(self._authentication_scope, cid)
        if retain_chat:
            if record is None:
                return None  # No allowed cleanup job exists: retain only in memory.
            if record.state != "completed":
                changes: dict[str, Any] = {"state": "retained", "verification_status": "retained_by_request"}
                if record.state == "running" and (record.lease_until or 0) > time.time():
                    # An accepted deletion cannot be reliably withdrawn. Prevent
                    # retries if it fails or its process disappears.
                    changes = {"error_code": "RETAIN_AFTER_CLAIM"}
                updated = repository.update(record.scope_id, cid, expected_version=record.version, **changes)
                record = updated or repository.get(record.scope_id, cid) or record
            observation = self._adopt_durable_record_locked(record)
            if record.state == "running" and record.error_code == "RETAIN_AFTER_CLAIM":
                self._pending_cleanup.pop(cid, None)
                self._cancel_delayed_cleanup_locked(cid)
                return replace(observation, state=CleanupState.RETAINED)
            return observation
        if record is None:
            record = self._enqueue_durable_locked(cid, delete_at, source)
        elif record.state in {"pending", "failed"} and explicit_delay and delete_at < record.due_at:
            updated = repository.update(record.scope_id, cid, expected_version=record.version,
                                        due_at=delete_at, expires_at=max(time.time(), delete_at) + RETENTION_SECONDS)
            record = updated or repository.get(record.scope_id, cid) or record
        observation = self._adopt_durable_record_locked(record)
        if record.state == "completed":
            return replace(observation, state=CleanupState.ALREADY_COMPLETED, idempotent=True)
        return observation

    def resume_cleanup_jobs(self) -> None:
        """Arm recovered deadlines only while an event loop and scope exist."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        with self._lock:
            self._arm_cleanup_jobs_locked(loop)

    def _arm_cleanup_jobs_locked(self, loop: asyncio.AbstractEventLoop, *, only_cid: str | None = None) -> None:
        if self._state_store is not None and self._authentication_scope is None:
            return
        for cid, pending in self._pending_cleanup.items():
            if only_cid is not None and cid != only_cid:
                continue
            if self._state_store is not None and pending.durable_version is None:
                continue
            if cid in self._inflight_cleanup or cid in self._delayed_cleanup or cid in self._suspended_cleanup:
                continue
            task = loop.create_task(self._delete_after_delay(cid, pending.delete_at, pending.authentication_generation))
            self._delayed_cleanup[cid] = task
            task.add_done_callback(partial(self._finish_delay, cid))

    def _resume_cleanup_job(self, cid: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        with self._lock:
            self._arm_cleanup_jobs_locked(loop, only_cid=cid)

    def authentication_generation(self) -> int:
        """Return an opaque context token without retaining authentication data."""
        with self._lock:
            return self._authentication_generation

    def authentication_context(self) -> tuple[str, int]:
        with self._lock:
            return self._authentication_context_id, self._authentication_generation

    def invalidate_authentication_context(self, chat_ids: tuple[str, ...] = ()) -> tuple[CleanupObservation, ...]:
        """Cancel old work, preserve locators and require explicit resumption."""
        with self._lock:
            if self._state_store is not None and self._authentication_scope is not None:
                for record in self._durable_records_locked():
                    if record.state in {"pending", "failed", "running"}:
                        self._state_store.cleanup_jobs.update(
                            record.scope_id, record.resource_id, expected_version=record.version,
                            state="cancelled", error_code="authentication_context_changed",
                            verification_status="not_observed", expires_at=time.time() + RETENTION_SECONDS,
                        )
            old_generation = self._authentication_generation
            old_ids = set(self._pending_cleanup) | set(self._inflight_cleanup) | set(chat_ids)
            tasks = [*self._inflight_cleanup.values(), *self._delayed_cleanup.values()]
            self._authentication_generation += 1
            self._pending_cleanup.clear()
            self._completed_cleanup.clear()
            self._inflight_cleanup.clear()
            self._delayed_cleanup.clear()
            self._authentication_scope = None
            observations = tuple(
                self._cancelled_for_generation_locked(cid, old_generation)
                for cid in sorted(old_ids)
                if is_valid_remote_chat_id(cid)
            )
        for task in tasks:
            loop = task.get_loop()
            if not task.done() and not loop.is_closed():
                loop.call_soon_threadsafe(task.cancel)
        return observations

    def record_authentication_context_change(
        self,
        cid: str | None,
        *,
        generation: int | None = None,
        diagnostic_id: str | None = None,
    ) -> CleanupObservation:
        """Record a late old resource without queuing it for the current account."""
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation()
        with self._lock:
            old_generation = self._authentication_generation - 1 if generation is None else generation
            return self._cancelled_for_generation_locked(cid, old_generation, diagnostic_id=diagnostic_id)

    def _store_observation_locked(self, cid: str, observation: CleanupObservation, generation: int) -> None:
        if generation >= self._observation_generations.get(cid, -1):
            self._cleanup_observations[cid] = observation
            self._observation_generations[cid] = generation

    def _cancelled_for_generation_locked(
        self,
        cid: str,
        generation: int,
        *,
        diagnostic_id: str | None = None,
    ) -> CleanupObservation:
        key = (generation, cid)
        observation = self._cancelled_cleanup.get(key)
        if observation is None:
            previous = self._cleanup_observations.get(cid)
            observation = CleanupObservation(
                state=CleanupState.CANCELLED,
                upstream_chat_id=cid,
                attempts=previous.attempts if previous is not None else 0,
                diagnostic_id=diagnostic_id or new_diagnostic_id(),
                cancellation_reason="authentication_context_changed",
            )
            self._cancelled_cleanup[key] = observation
            logger.info(
                "Remote cleanup cancelled cid=%s reason=authentication_context_changed diagnostic_id=%s",
                cid,
                observation.diagnostic_id,
            )
        self._store_observation_locked(cid, observation, generation)
        return observation

    def schedule_cleanup_from_response(
        self,
        response: Any,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        source: str = "",
    ) -> str | None:
        """登记 response 产生的远端 chat，默认稍后自动删除。"""
        cid = extract_remote_chat_id(response)
        if cid:
            self.schedule_cleanup(
                cid,
                retain_chat=retain_chat,
                delete_after_seconds=delete_after_seconds,
                source=source,
            )
        return cid

    def schedule_cleanup_result_from_response(
        self,
        response: Any,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        source: str = "",
        authentication_generation: int | None = None,
    ) -> CleanupObservation:
        """Schedule from a response and return the observable policy result."""
        cid = extract_remote_chat_id(response)
        return self.schedule_cleanup_result(
            cid,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=source,
            authentication_generation=authentication_generation,
        )

    def schedule_cleanup(
        self,
        cid: str | None,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        source: str = "",
    ) -> None:
        """登记远端 Gemini chat 的自动删除任务。"""
        self.schedule_cleanup_result(
            cid,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=source,
        )

    def schedule_cleanup_result(
        self,
        cid: str | None,
        retain_chat: bool = False,
        delete_after_seconds: int | None = None,
        source: str = "",
        authentication_generation: int | None = None,
    ) -> CleanupObservation:
        """Register one idempotent cleanup decision and expose its state."""
        if cid is None:
            return CleanupObservation(source=source)
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(
                state=CleanupState.INVALID_ID,
                source=source,
            )

        delete_at = 0.0
        explicit_delay = delete_after_seconds is not None
        if not retain_chat:
            if delete_after_seconds is None and self._retention_provider is not None:
                delete_after_seconds = self._retention_provider()
            ttl = self._default_retention if delete_after_seconds is None else max(0, delete_after_seconds)
            delete_at = time.time() + ttl

        with self._lock:
            generation = self._authentication_generation if authentication_generation is None else authentication_generation
            if generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, generation)
            try:
                durable = self._durable_schedule_locked(
                    cid, retain_chat=retain_chat, explicit_delay=explicit_delay, delete_at=delete_at, source=source,
                )
            except Exception:  # noqa: BLE001 - never lose a generated artifact to cleanup bookkeeping
                return self._storage_failure_locked(cid, source)
            if durable is not None:
                self._store_observation_locked(cid, durable, generation)
                try:
                    self._arm_cleanup_jobs_locked(asyncio.get_running_loop())
                except RuntimeError:
                    pass
                return durable
            completed = self._completed_cleanup.get(cid)
            if completed is not None:
                observation = replace(
                    completed,
                    state=CleanupState.ALREADY_COMPLETED,
                    idempotent=True,
                )
                self._store_observation_locked(cid, observation, generation)
                return observation

            if retain_chat:
                self._pending_cleanup.pop(cid, None)
                self._cancel_delayed_cleanup_locked(cid)
                observation = CleanupObservation(
                    state=CleanupState.RETAINED,
                    upstream_chat_id=cid,
                    source=source,
                )
                self._store_observation_locked(cid, observation, generation)
                return observation

            pending = self._pending_cleanup.get(cid)
            rescheduled = pending is not None and explicit_delay and delete_at < pending.delete_at
            if pending is not None and not rescheduled:
                previous = self._cleanup_observations.get(cid)
                if previous is not None and previous.state is CleanupState.FAILED:
                    observation = replace(previous, idempotent=True)
                else:
                    observation = CleanupObservation(
                        state=CleanupState.PENDING,
                        upstream_chat_id=cid,
                        attempts=pending.attempts,
                        diagnostic_id=pending.last_diagnostic_id,
                        idempotent=True,
                        source=pending.source,
                        delete_at=pending.delete_at,
                    )
                self._store_observation_locked(cid, observation, generation)
                return observation

            if pending is None:
                pending = CleanupTask(
                    delete_at=delete_at,
                    source=source,
                    authentication_generation=generation,
                )
                self._pending_cleanup[cid] = pending
            else:
                pending.delete_at = delete_at
                self._cancel_delayed_cleanup_locked(cid)
            observation = CleanupObservation(
                state=CleanupState.PENDING,
                upstream_chat_id=cid,
                attempts=pending.attempts,
                diagnostic_id=pending.last_diagnostic_id,
                idempotent=rescheduled,
                source=pending.source,
                delete_at=delete_at,
            )
            self._store_observation_locked(cid, observation, generation)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return observation
        task = loop.create_task(self._delete_after_delay(cid, delete_at, generation))
        with self._lock:
            pending = self._pending_cleanup.get(cid)
            if generation == self._authentication_generation and pending is not None and pending.delete_at == delete_at:
                self._delayed_cleanup[cid] = task
            else:
                task.cancel()
        task.add_done_callback(lambda completed: self._finish_delay(cid, completed))
        return observation

    def _cancel_delayed_cleanup_locked(self, cid: str) -> None:
        task = self._delayed_cleanup.pop(cid, None)
        if task is None or task.done() or task.get_loop().is_closed():
            return
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if task.get_loop() is current_loop:
            task.cancel()
        else:
            task.get_loop().call_soon_threadsafe(task.cancel)

    def _finish_delay(self, cid: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            if self._delayed_cleanup.get(cid) is task:
                self._delayed_cleanup.pop(cid, None)
        if not task.cancelled():
            task.exception()
        if self._state_store is not None and not task.cancelled() and not task.get_loop().is_closed():
            task.get_loop().call_soon(self._resume_cleanup_job, cid)

    async def _delete_after_delay(self, cid: str, delete_at: float, generation: int | None = None) -> None:
        """延迟删除任务"""
        await asyncio.sleep(max(0, delete_at - time.time()))

        with self._lock:
            pending = self._pending_cleanup.get(cid)
            if (
                not pending
                or pending.delete_at != delete_at
                or (generation is not None and pending.authentication_generation != generation)
            ):
                return

            expected_due = (pending, delete_at, pending.attempts)

        await self._delete_chat_result(
            cid,
            authentication_generation=pending.authentication_generation,
            expected_due=expected_due,
        )

    async def delete_chat(
        self,
        cid: str | None,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
    ) -> bool:
        """立即删除远端 Gemini chat；重复成功删除视为幂等成功。"""
        observation = await self.delete_chat_result(
            cid,
            client=client,
            client_initializer=client_initializer,
        )
        return observation.state in {
            CleanupState.COMPLETED,
            CleanupState.ALREADY_COMPLETED,
        }

    async def delete_chat_result(
        self,
        cid: str | None,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
        *,
        authentication_generation: int | None = None,
        source: str = "",
        allow_durable: bool = False,
    ) -> CleanupObservation:
        """Delete once per upstream ID and return public-safe cleanup evidence."""
        observation = await self._delete_chat_result(
            cid,
            client=client,
            client_initializer=client_initializer,
            authentication_generation=authentication_generation,
            source=source,
            allow_durable=allow_durable,
        )
        assert observation is not None
        return observation

    async def _delete_chat_result(
        self,
        cid: str | None,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
        *,
        authentication_generation: int | None = None,
        expected_due: tuple[CleanupTask, float, int] | None = None,
        source: str = "",
        allow_durable: bool = False,
    ) -> CleanupObservation | None:
        """Claim automatic work only while its original due decision is current."""
        if cid is None:
            return CleanupObservation(source=source)
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(state=CleanupState.INVALID_ID, source=source)

        with self._lock:
            generation = self._authentication_generation if authentication_generation is None else authentication_generation
            if generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, generation)
            if self._state_store is not None and self._authentication_scope is None:
                return CleanupObservation(state=CleanupState.CANCELLED, upstream_chat_id=cid,
                                          cancellation_reason="authentication_context_unavailable", source=source)
            if expected_due is not None:
                expected_task, delete_at, attempts = expected_due
                pending = self._pending_cleanup.get(cid)
                # Validate and claim under the same lock. A retained, replaced,
                # rescheduled, or already attempted job invalidates this snapshot.
                if (
                    pending is not expected_task
                    or pending.delete_at != delete_at
                    or pending.delete_at > time.time()
                    or pending.attempts != attempts
                    or cid in self._inflight_cleanup
                ):
                    return None
            completed = self._completed_cleanup.get(cid)
            if completed is not None:
                observation = replace(
                    completed,
                    state=CleanupState.ALREADY_COMPLETED,
                    idempotent=True,
                )
                self._store_observation_locked(cid, observation, generation)
                return observation

            task = self._inflight_cleanup.get(cid)
            joined_existing = task is not None
            if task is None:
                pending = self._pending_cleanup.get(cid)
                delete_source = pending.source if pending is not None else source
                try:
                    claim = self._claim_durable_locked(cid, expected_due=expected_due,
                                                      allow_durable=allow_durable, source=delete_source)
                except Exception:  # noqa: BLE001 - failed authority must prevent the remote mutation
                    return self._storage_failure_locked(cid, delete_source)
                if isinstance(claim, CleanupObservation):
                    return None if expected_due is not None else claim
                if claim is not None:
                    task = asyncio.create_task(self._execute_durable_delete(
                        claim, client=client, client_initializer=client_initializer,
                        authentication_generation=generation,
                    ))
                else:
                    task = asyncio.create_task(
                        self._execute_delete(
                            cid,
                            client=client,
                            client_initializer=client_initializer,
                            authentication_generation=generation,
                            source=delete_source,
                        )
                    )
                self._inflight_cleanup[cid] = task
                task.add_done_callback(lambda completed: self._finish_delete(cid, completed))

        # Stopping one caller's wait must not cancel a shared upstream mutation.
        observation = await asyncio.shield(task)

        if joined_existing:
            if observation.state is CleanupState.COMPLETED:
                return replace(
                    observation,
                    state=CleanupState.ALREADY_COMPLETED,
                    idempotent=True,
                )
            return replace(observation, idempotent=True)
        return observation

    def _claim_durable_locked(
        self, cid: str, *, expected_due: tuple[CleanupTask, float, int] | None,
        allow_durable: bool, source: str,
    ) -> CleanupJobRecord | CleanupObservation | None:
        if self._state_store is None or self._authentication_scope is None:
            return None
        repository = self._state_store.cleanup_jobs
        record = repository.get(self._authentication_scope, cid)
        if record is None:
            if not allow_durable:
                return None  # An arbitrary explicit delete never becomes an automatic retry.
            record = self._enqueue_durable_locked(cid, time.time(), source)
        if expected_due is not None and record.version != expected_due[0].durable_version:
            return replace(self._adopt_durable_record_locked(record), idempotent=True)
        now = time.time()
        if record.state == "running" and record.error_code == "RETAIN_AFTER_CLAIM":
            if (record.lease_until or 0) <= now:
                updated = repository.update(record.scope_id, cid, expected_version=record.version,
                                            state="retained", verification_status="retained_after_claim")
                record = updated or repository.get(record.scope_id, cid) or record
            observation = self._adopt_durable_record_locked(record)
            return replace(observation, state=CleanupState.RETAINED, idempotent=True)
        if expected_due is None and record.state in {"pending", "failed"} and record.due_at > now:
            updated = repository.update(record.scope_id, cid, expected_version=record.version, due_at=now)
            record = updated or repository.get(record.scope_id, cid) or record
        claimed = repository.claim(record.scope_id, cid, expected_version=record.version,
                                   lease_id="lease_" + uuid.uuid4().hex,
                                   lease_until=now + self._lease_seconds, now=now)
        if claimed is None:
            observed = repository.get(record.scope_id, cid) or record
            observation = self._adopt_durable_record_locked(observed)
            if observed.state == "completed":
                observation = replace(observation, state=CleanupState.ALREADY_COMPLETED)
            return replace(observation, idempotent=True)
        self._adopt_durable_record_locked(claimed)
        return claimed

    async def _renew_durable_lease(self, record: CleanupJobRecord, worker: asyncio.Task[Any]) -> None:
        assert self._state_store is not None and record.lease_id is not None
        try:
            while True:
                await asyncio.sleep(self._lease_seconds / 3)
                renewed = self._state_store.cleanup_jobs.renew(
                    record.scope_id, record.resource_id, lease_id=record.lease_id,
                    lease_until=time.time() + self._lease_seconds,
                )
                if renewed is None:
                    worker.cancel()
                    return
        except Exception:  # noqa: BLE001 - losing durable authority must stop the worker
            worker.cancel()

    async def _execute_durable_delete(
        self, record: CleanupJobRecord, *, client: Any,
        client_initializer: Callable[[], Any] | None, authentication_generation: int,
    ) -> CleanupObservation:
        assert self._state_store is not None
        worker = asyncio.current_task()
        assert worker is not None
        heartbeat = asyncio.create_task(self._renew_durable_lease(record, worker))
        verified = False
        error_code: str | None = None
        verification_status = "not_observed"
        try:
            if client is None:
                resolver = client_initializer or self._client_provider
                if resolver is not None:
                    client = resolver()
                    if inspect.isawaitable(client):
                        client = await client
            with self._lock:
                if authentication_generation != self._authentication_generation:
                    return self._cancelled_for_generation_locked(record.resource_id, authentication_generation)
                current = self._state_store.cleanup_jobs.get(record.scope_id, record.resource_id)
                if current is None or current.state != "running" or current.lease_id != record.lease_id:
                    return self._adopt_durable_record_locked(current) if current is not None else CleanupObservation(
                        state=CleanupState.CANCELLED, upstream_chat_id=record.resource_id,
                        cancellation_reason="cleanup_authority_lost",
                    )
            from .services.history import delete_chat_result, observe_chat_absence

            # A process may have died after deletion and before its terminal
            # write. Positive absence can complete recovery without deleting twice.
            if record.attempts > 1:
                absent, _details = await observe_chat_absence(client, record.resource_id)
                verified = absent is True
            if not verified:
                with self._lock:
                    if authentication_generation != self._authentication_generation:
                        return self._cancelled_for_generation_locked(record.resource_id, authentication_generation)
                    current = self._state_store.cleanup_jobs.get(record.scope_id, record.resource_id)
                    if current is None or current.state != "running" or current.lease_id != record.lease_id:
                        return self._adopt_durable_record_locked(current) if current is not None else CleanupObservation(
                            state=CleanupState.CANCELLED, upstream_chat_id=record.resource_id,
                            cancellation_reason="cleanup_authority_lost",
                        )
                result = await delete_chat_result(client, record.resource_id)
                verification_status = result.meta.verification_status
                verified = bool(result.ok and result.data is not None
                                and result.data.get("deleted") is True
                                and verification_status == "verified_absent")
                if not verified:
                    error_code = result.error.code.value if result.error is not None else "VERIFICATION_FAILED"
        except asyncio.CancelledError:
            with self._lock:
                if authentication_generation == self._authentication_generation:
                    self._suspended_cleanup.add(record.resource_id)
            error_code = "CANCELLED"
        except Exception as error:  # noqa: BLE001 - persist stable codes only, never raw account responses
            error_code = type(error).__name__
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            with self._lock:
                if authentication_generation != self._authentication_generation:
                    return self._cancelled_for_generation_locked(record.resource_id, authentication_generation)
                current = self._state_store.cleanup_jobs.get(record.scope_id, record.resource_id)
                if current is not None and current.error_code == "RETAIN_AFTER_CLAIM" and not verified:
                    state, verification_status = "retained", "retained_after_claim"
                else:
                    state = "completed" if verified else "failed"
                now = time.time()
                updated = self._state_store.cleanup_jobs.update(
                    record.scope_id, record.resource_id, lease_id=record.lease_id,
                    state=state, verification_status="verified_absent" if verified else verification_status,
                    diagnostic_id=None if verified else new_diagnostic_id(), error_code=error_code,
                    due_at=now + min(3600, 2 ** min(record.attempts, 10)), expires_at=now + RETENTION_SECONDS,
                )
                current = updated or self._state_store.cleanup_jobs.get(record.scope_id, record.resource_id)
                if current is None:
                    return CleanupObservation(state=CleanupState.CANCELLED, upstream_chat_id=record.resource_id,
                                              cancellation_reason="cleanup_authority_lost", source=record.source)
                return self._adopt_durable_record_locked(current)
        except Exception:  # noqa: BLE001 - preserve the locator when durable terminal storage fails
            with self._lock:
                if authentication_generation != self._authentication_generation:
                    return self._cancelled_for_generation_locked(record.resource_id, authentication_generation)
                return self._storage_failure_locked(record.resource_id, record.source, attempts=record.attempts)

    def _finish_delete(self, cid: str, task: asyncio.Task[CleanupObservation]) -> None:
        """Release manager-owned work even when every caller stopped waiting."""
        with self._lock:
            if self._inflight_cleanup.get(cid) is task:
                self._inflight_cleanup.pop(cid, None)
        if not task.cancelled():
            # Retrieve an unexpected exception so an abandoned task cannot emit
            # an unhandled-task warning. Normal failures are recorded by owner.
            task.exception()
        if self._state_store is not None and not task.cancelled() and not task.get_loop().is_closed():
            task.get_loop().call_soon(self._resume_cleanup_job, cid)

    async def _execute_delete(
        self,
        cid: str,
        *,
        client: Any,
        client_initializer: Callable[[], Any] | None,
        authentication_generation: int,
        source: str,
    ) -> CleanupObservation:
        with self._lock:
            if authentication_generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, authentication_generation)
            pending = self._pending_cleanup.get(cid)
            attempts = (pending.attempts if pending is not None else 0) + 1
            if pending is not None:
                pending.attempts = attempts
            self._store_observation_locked(
                cid,
                CleanupObservation(
                    state=CleanupState.PENDING,
                    upstream_chat_id=cid,
                    attempts=attempts,
                    source=source,
                ),
                authentication_generation,
            )

        try:
            if client is None:
                resolver = client_initializer or self._client_provider
                if resolver is not None:
                    client = resolver()
                    if inspect.isawaitable(client):
                        client = await client
            with self._lock:
                if authentication_generation != self._authentication_generation:
                    return self._cancelled_for_generation_locked(cid, authentication_generation)

            # Import after module initialization: services.lifecycle depends on
            # this manager. History owns the shared, complete read-back contract.
            from .services.history import delete_chat_result

            result = await delete_chat_result(client, cid)
            if not (
                result.ok
                and result.data is not None
                and result.data.get("deleted") is True
                and result.meta.verification_status == "verified_absent"
            ):
                raise RuntimeError(
                    f"Chat deletion was not verified: {result.meta.verification_status}",
                )
        except asyncio.CancelledError as error:
            observation = self._record_failure(
                cid,
                attempts=attempts,
                source=source,
                error=error,
                authentication_generation=authentication_generation,
            )
            if observation.state is CleanupState.CANCELLED:
                return observation
            raise
        except Exception as error:  # noqa: BLE001 - persist arbitrary upstream failure evidence
            return self._record_failure(
                cid,
                attempts=attempts,
                source=source,
                error=error,
                authentication_generation=authentication_generation,
            )

        observation = CleanupObservation(
            state=CleanupState.COMPLETED,
            upstream_chat_id=cid,
            attempts=attempts,
            source=source,
        )
        with self._lock:
            if authentication_generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, authentication_generation)
            self._pending_cleanup.pop(cid, None)
            self._cancel_delayed_cleanup_locked(cid)
            self._completed_cleanup[cid] = observation
            self._store_observation_locked(cid, observation, authentication_generation)

        logger.info("已删除远端 Gemini 对话: %s", cid)
        return observation

    def _record_failure(
        self,
        cid: str,
        *,
        attempts: int,
        source: str,
        error: BaseException,
        authentication_generation: int | None = None,
    ) -> CleanupObservation:
        diagnostic_id = new_diagnostic_id()
        observation = CleanupObservation(
            state=CleanupState.FAILED,
            upstream_chat_id=cid,
            attempts=attempts,
            diagnostic_id=diagnostic_id,
            source=source,
        )
        with self._lock:
            generation = self._authentication_generation if authentication_generation is None else authentication_generation
            if generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, generation)
            pending = self._pending_cleanup.get(cid)
            if pending is None:
                pending = CleanupTask(delete_at=time.time(), source=source, authentication_generation=generation)
                self._pending_cleanup[cid] = pending
            pending.attempts = attempts
            pending.last_diagnostic_id = diagnostic_id
            self._store_observation_locked(cid, observation, generation)
        logger.warning(
            "删除远端 Gemini 对话失败 cid=%s diagnostic_id=%s error_type=%s error=%r",
            cid,
            diagnostic_id,
            type(error).__name__,
            error,
        )
        return observation

    def record_cleanup_failure(
        self,
        cid: str | None,
        error: BaseException,
        *,
        source: str = "",
    ) -> CleanupObservation:
        """Persist a failure that occurred before the upstream delete call."""
        if cid is None:
            return CleanupObservation(source=source)
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(
                state=CleanupState.INVALID_ID,
                source=source,
            )
        with self._lock:
            pending = self._pending_cleanup.get(cid)
            attempts = (pending.attempts if pending is not None else 0) + 1
        return self._record_failure(
            cid,
            attempts=attempts,
            source=source,
            error=error,
        )

    def record_cleanup_wait_timeout(
        self,
        cid: str,
        *,
        source: str = "",
        authentication_generation: int | None = None,
    ) -> CleanupObservation:
        """Record an expired caller wait without cancelling or retrying its worker."""
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(state=CleanupState.INVALID_ID, source=source)
        with self._lock:
            generation = self._authentication_generation if authentication_generation is None else authentication_generation
            if generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, generation)
            observed = self._cleanup_observations.get(cid)
            if observed is not None and observed.state is not CleanupState.PENDING:
                return observed
            attempts = observed.attempts if observed is not None else 0
            diagnostic_id = (observed.diagnostic_id if observed is not None else None) or new_diagnostic_id()
            pending = self._pending_cleanup.get(cid)
            if pending is None:
                pending = CleanupTask(
                    delete_at=time.time(),
                    source=observed.source if observed is not None else source,
                    attempts=attempts,
                    authentication_generation=generation,
                )
                self._pending_cleanup[cid] = pending
            pending.last_diagnostic_id = diagnostic_id
            observation = CleanupObservation(
                state=CleanupState.PENDING,
                upstream_chat_id=cid,
                attempts=attempts,
                diagnostic_id=diagnostic_id,
                source=pending.source,
                delete_at=pending.delete_at,
            )
            self._store_observation_locked(cid, observation, generation)
        logger.warning("Remote cleanup wait timed out cid=%s diagnostic_id=%s", cid, diagnostic_id)
        return observation

    async def cleanup_due_chats(
        self,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
        *,
        authentication_generation: int | None = None,
    ) -> int:
        """清理已经到期的远端 Gemini chat。"""
        results = await self.cleanup_due_chat_results(
            client=client,
            client_initializer=client_initializer,
            authentication_generation=authentication_generation,
        )
        return sum(result.state is CleanupState.COMPLETED for result in results)

    async def cleanup_due_chat_results(
        self,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
        *,
        authentication_generation: int | None = None,
        limit: int | None = None,
    ) -> tuple[CleanupObservation, ...]:
        """Clean due chats and retain a diagnosable result for every attempt."""
        now = time.time()
        with self._lock:
            if authentication_generation is not None and authentication_generation != self._authentication_generation:
                return ()
            self._refresh_durable_jobs_locked()
            due_cids = [
                (cid, data, data.delete_at, data.attempts)
                for cid, data in self._pending_cleanup.items()
                if data.delete_at <= now and cid not in self._inflight_cleanup
            ]
            if limit is not None:
                due_cids = due_cids[:max(0, limit)]

        results = []
        for cid, pending, delete_at, attempts in due_cids:
            observation = await self._delete_chat_result(
                cid,
                client=client,
                client_initializer=client_initializer,
                authentication_generation=pending.authentication_generation,
                expected_due=(pending, delete_at, attempts),
            )
            if observation is not None:
                results.append(observation)
        return tuple(results)

    def list_pending_cleanup(self) -> dict[str, CleanupTask]:
        """返回待自动删除的远端 chat。"""
        with self._lock:
            self._refresh_durable_jobs_locked()
            return dict(self._pending_cleanup)

    def list_durable_jobs(self, *, limit: int = 50, offset: int = 0,
                          states: tuple[str, ...] = ()) -> tuple[CleanupJobRecord, ...]:
        with self._lock:
            if self._state_store is None or self._authentication_scope is None:
                return ()
            return self._state_store.cleanup_jobs.list(self._authentication_scope,
                                                       limit=limit, offset=offset, states=states)

    def get_durable_job(self, job_id: str) -> CleanupJobRecord | None:
        with self._lock:
            if self._state_store is None or self._authentication_scope is None:
                return None
            return self._state_store.cleanup_jobs.get_by_job_id(self._authentication_scope, job_id)

    def cancel_durable_job(self, job_id: str) -> CleanupJobRecord | None:
        """Withdraw future authority; an upstream RPC may already have started."""
        with self._lock:
            if self._state_store is None or self._authentication_scope is None:
                return None
            record = self._state_store.cleanup_jobs.get_by_job_id(self._authentication_scope, job_id)
            if record is None or record.state in {"completed", "retained", "cancelled"}:
                return record
            updated = self._state_store.cleanup_jobs.update(
                record.scope_id, record.resource_id, expected_version=record.version,
                state="cancelled", error_code="cancelled_by_request", verification_status="not_observed",
                expires_at=time.time() + RETENTION_SECONDS,
            )
            current = updated or self._state_store.cleanup_jobs.get(record.scope_id, record.resource_id)
            if current is not None:
                self._adopt_durable_record_locked(current)
            return current

    async def retry_durable_job(self, job_id: str, client: Any = None) -> CleanupObservation | None:
        """Retry only a registered, still allowed resource in the current scope."""
        record = self.get_durable_job(job_id)
        if record is None:
            return None
        return await self.delete_chat_result(record.resource_id, client=client)

    def durable_scope_available(self) -> bool:
        with self._lock:
            return self._state_store is not None and self._authentication_scope is not None

    def authentication_scope_id(self) -> str | None:
        with self._lock:
            return self._authentication_scope

    def credentials_match_scope(self, psid: str) -> bool | None:
        """Compare effective credentials without retaining or exposing them."""
        with self._lock:
            if self._state_store is None or self._authentication_scope is None:
                return None
            return authentication_scope(psid, store=self._state_store) == self._authentication_scope

    def get_cleanup_observation(
        self,
        cid: str | None,
    ) -> CleanupObservation | None:
        """Return the latest observable result without exposing raw failures."""
        if not is_valid_remote_chat_id(cid):
            return None
        with self._lock:
            return self._cleanup_observations.get(cid)

    def list_cleanup_observations(self) -> dict[str, CleanupObservation]:
        """Return a snapshot of pending, completed, retained, and failed states."""
        with self._lock:
            return dict(self._cleanup_observations)
