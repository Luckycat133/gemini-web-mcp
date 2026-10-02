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
from typing import Any

from .constants import DEFAULT_CHAT_RETENTION_SECONDS
from .domain import (
    CleanupObservation,
    CleanupState,
    is_valid_remote_chat_id,
    new_diagnostic_id,
)

logger = logging.getLogger(__name__)


@dataclass
class CleanupTask:
    """清理任务数据结构"""

    delete_at: float
    source: str = ""
    attempts: int = 0
    last_diagnostic_id: str | None = None
    authentication_generation: int = 0


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
            old_generation = self._authentication_generation
            old_ids = set(self._pending_cleanup) | set(self._inflight_cleanup) | set(chat_ids)
            tasks = [*self._inflight_cleanup.values(), *self._delayed_cleanup.values()]
            self._authentication_generation += 1
            self._pending_cleanup.clear()
            self._completed_cleanup.clear()
            self._inflight_cleanup.clear()
            self._delayed_cleanup.clear()
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
        if not retain_chat:
            if delete_after_seconds is None and self._retention_provider is not None:
                delete_after_seconds = self._retention_provider()
            ttl = self._default_retention if delete_after_seconds is None else max(0, delete_after_seconds)
            delete_at = time.time() + ttl

        with self._lock:
            generation = self._authentication_generation if authentication_generation is None else authentication_generation
            if generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, generation)
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
                observation = CleanupObservation(
                    state=CleanupState.RETAINED,
                    upstream_chat_id=cid,
                    source=source,
                )
                self._store_observation_locked(cid, observation, generation)
                return observation

            pending = self._pending_cleanup.get(cid)
            if pending is not None:
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

            pending_task = CleanupTask(
                delete_at=delete_at,
                source=source,
                authentication_generation=generation,
            )
            self._pending_cleanup[cid] = pending_task
            observation = CleanupObservation(
                state=CleanupState.PENDING,
                upstream_chat_id=cid,
                source=source,
                delete_at=delete_at,
            )
            self._store_observation_locked(cid, observation, generation)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return observation
        task = loop.create_task(self._delete_after_delay(cid, delete_at, generation))
        with self._lock:
            if generation == self._authentication_generation and cid in self._pending_cleanup:
                self._delayed_cleanup[cid] = task
            else:
                task.cancel()
        task.add_done_callback(lambda completed: self._finish_delay(cid, completed))
        return observation

    def _finish_delay(self, cid: str, task: asyncio.Task[None]) -> None:
        with self._lock:
            if self._delayed_cleanup.get(cid) is task:
                self._delayed_cleanup.pop(cid, None)
        if not task.cancelled():
            task.exception()

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

        await self.delete_chat_result(cid, authentication_generation=generation)

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
    ) -> CleanupObservation:
        """Delete once per upstream ID and return public-safe cleanup evidence."""
        if cid is None:
            return CleanupObservation()
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(state=CleanupState.INVALID_ID)

        with self._lock:
            generation = self._authentication_generation if authentication_generation is None else authentication_generation
            if generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, generation)
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
                task = asyncio.create_task(
                    self._execute_delete(
                        cid,
                        client=client,
                        client_initializer=client_initializer,
                        authentication_generation=generation,
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

    def _finish_delete(self, cid: str, task: asyncio.Task[CleanupObservation]) -> None:
        """Release manager-owned work even when every caller stopped waiting."""
        with self._lock:
            if self._inflight_cleanup.get(cid) is task:
                self._inflight_cleanup.pop(cid, None)
        if not task.cancelled():
            # Retrieve an unexpected exception so an abandoned task cannot emit
            # an unhandled-task warning. Normal failures are recorded by owner.
            task.exception()

    async def _execute_delete(
        self,
        cid: str,
        *,
        client: Any,
        client_initializer: Callable[[], Any] | None,
        authentication_generation: int,
    ) -> CleanupObservation:
        with self._lock:
            if authentication_generation != self._authentication_generation:
                return self._cancelled_for_generation_locked(cid, authentication_generation)
            pending = self._pending_cleanup.get(cid)
            source = pending.source if pending is not None else ""
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
    ) -> tuple[CleanupObservation, ...]:
        """Clean due chats and retain a diagnosable result for every attempt."""
        now = time.time()
        with self._lock:
            if authentication_generation is not None and authentication_generation != self._authentication_generation:
                return ()
            due_cids = [(cid, data.authentication_generation) for cid, data in self._pending_cleanup.items() if data.delete_at <= now]

        results = []
        for cid, generation in due_cids:
            results.append(
                await self.delete_chat_result(
                    cid,
                    client=client,
                    client_initializer=client_initializer,
                    authentication_generation=generation,
                )
            )
        return tuple(results)

    def list_pending_cleanup(self) -> dict[str, CleanupTask]:
        """返回待自动删除的远端 chat。"""
        with self._lock:
            return dict(self._pending_cleanup)

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
