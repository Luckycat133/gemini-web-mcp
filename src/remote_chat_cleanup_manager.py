"""
远程聊天清理管理器 - 负责远端 Gemini chat 的自动删除调度和执行
"""

import asyncio
import inspect
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any
from pathlib import Path

from .constants import DEFAULT_CHAT_RETENTION_SECONDS
from .infrastructure.cleanup_store import CleanupStore
from .infrastructure.account_identity import current_account_key
from .domain import (
    CleanupObservation,
    CleanupState,
    is_valid_remote_chat_id,
    new_diagnostic_id,
)

logger = logging.getLogger(__name__)


def default_account_key() -> str | None:
    return current_account_key()


def default_cleanup_guard(cid: str, *, account_key: str | None = None) -> bool:
    """Protected long operations must keep their remote chat until released."""
    from .infrastructure.operation_store import OperationStore
    from .session_manager import session_cleanup_protected

    return session_cleanup_protected(cid, account_key=account_key) or OperationStore().is_cleanup_protected(cid)


@dataclass
class CleanupTask:
    """清理任务数据结构"""

    delete_at: float
    source: str = ""
    attempts: int = 0
    last_diagnostic_id: str | None = None


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
        store_path: Path | str | None = None,
        account_key_provider: Callable[[], str | None] | None = None,
        cleanup_guard: Callable[[str], bool] | None = None,
    ):
        self._pending_cleanup: dict[str, CleanupTask] = {}
        self._cleanup_observations: dict[str, CleanupObservation] = {}
        self._completed_cleanup: dict[str, CleanupObservation] = {}
        self._inflight_cleanup: dict[str, asyncio.Task[CleanupObservation]] = {}
        self._lock = threading.Lock()
        self._default_retention = default_retention_seconds
        self._client_provider = client_provider
        self._retention_provider = retention_provider
        self._store = CleanupStore(store_path)
        self._account_key_provider = account_key_provider or default_account_key
        self._cleanup_guard = cleanup_guard or (lambda cid: default_cleanup_guard(
            cid, account_key=self._account_key_provider(),
        ))
        self._active_account: str | None = None

    def _account(self) -> str | None:
        account = self._account_key_provider()
        with self._lock:
            if account != self._active_account:
                self._pending_cleanup.clear()
                self._cleanup_observations.clear()
                self._completed_cleanup.clear()
                self._inflight_cleanup.clear()
                self._active_account = account
        return account

    def record_explicit_verified_absence(self, cid: str) -> None:
        """Record an explicit Task deletion only for a previously owned queue entry."""
        account = self._account()
        if not account or not self._store.record_explicit_verified_absence(account, cid):
            return
        observation = CleanupObservation(state=CleanupState.COMPLETED, upstream_chat_id=cid)
        with self._lock:
            if self._account_key_provider() == account:
                self._pending_cleanup.pop(cid, None)
                self._cleanup_observations[cid] = observation
                self._completed_cleanup[cid] = observation

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
    ) -> CleanupObservation:
        """Schedule from a response and return the observable policy result."""
        cid = extract_remote_chat_id(response)
        return self.schedule_cleanup_result(
            cid,
            retain_chat=retain_chat,
            delete_after_seconds=delete_after_seconds,
            source=source,
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
        dispatch: bool = True,
        reschedule_pending: bool = False,
        _owner_account: str | None = None,
    ) -> CleanupObservation:
        """Register one idempotent cleanup decision and expose its state."""
        if cid is None:
            return CleanupObservation(source=source)
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(
                state=CleanupState.INVALID_ID,
                source=source,
            )

        active_account = self._account()
        account = _owner_account or active_account
        if not account:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid, source=source)

        delete_at = 0.0
        if not retain_chat:
            if delete_after_seconds is None and self._retention_provider is not None:
                delete_after_seconds = self._retention_provider()
            ttl = self._default_retention if delete_after_seconds is None else max(0, delete_after_seconds)
            delete_at = time.time() + ttl

        self._store.schedule(account, cid, delete_at, source, retain=retain_chat, override_retained=not retain_chat,
                             reschedule_pending=reschedule_pending)
        record = self._store.get(account, cid)
        if record and record["state"] == "completed":
            return CleanupObservation(state=CleanupState.ALREADY_COMPLETED, upstream_chat_id=cid, idempotent=True)
        if account != active_account:
            # A late frame still belongs to the account that started its
            # request. Persist metadata for that account without touching the
            # newly active account's caches or dispatching a delete with it.
            state = CleanupState.RETAINED if record and record["state"] == "retained" else CleanupState.PENDING
            return CleanupObservation(state=state, upstream_chat_id=cid, source=source,
                                      delete_at=record["delete_at"] if record else delete_at)
        if retain_chat and record and record["state"] == "pending":
            # Another worker has already claimed deletion; an in-flight remote
            # request cannot safely be represented as a retained conversation.
            return CleanupObservation(
                state=CleanupState.FAILED, upstream_chat_id=cid, source=source,
                diagnostic_id=new_diagnostic_id(),
            )
        if record and record["state"] == "retained":
            with self._lock:
                self._pending_cleanup.pop(cid, None)
                observation = CleanupObservation(state=CleanupState.RETAINED, upstream_chat_id=cid, source=source)
                self._cleanup_observations[cid] = observation
            return observation
        if record:
            delete_at = record["delete_at"]

        with self._lock:
            if retain_chat:
                self._pending_cleanup.pop(cid, None)
                observation = CleanupObservation(
                    state=CleanupState.RETAINED,
                    upstream_chat_id=cid,
                    source=source,
                )
                self._cleanup_observations[cid] = observation
                return observation

            pending = self._pending_cleanup.get(cid)
            if pending is not None and pending.delete_at != delete_at:
                # Invalidate the old timer when an explicit policy changes the deadline.
                self._pending_cleanup.pop(cid, None)
                pending = None
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
                self._cleanup_observations[cid] = observation
                return observation

            task = CleanupTask(
                delete_at=delete_at,
                source=source,
            )
            self._pending_cleanup[cid] = task
            observation = CleanupObservation(
                state=CleanupState.PENDING,
                upstream_chat_id=cid,
                source=source,
                delete_at=delete_at,
            )
            self._cleanup_observations[cid] = observation

        if not dispatch:
            return observation
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return observation
        loop.create_task(self._delete_after_delay(cid, delete_at, account=account))
        return observation

    async def _delete_after_delay(self, cid: str, delete_at: float, *, account: str | None = None) -> None:
        """延迟删除任务"""
        await asyncio.sleep(max(0, delete_at - time.time()))

        with self._lock:
            pending = self._pending_cleanup.get(cid)
            if not pending or pending.delete_at != delete_at:
                return

        if account is not None and self._account_key_provider() != account:
            return
        await self.delete_chat_result(cid, _expected_account=account)

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
        _expected_account: str | None = None,
    ) -> CleanupObservation:
        """Delete once per upstream ID and return public-safe cleanup evidence."""
        if cid is None:
            return CleanupObservation()
        if not is_valid_remote_chat_id(cid):
            return CleanupObservation(state=CleanupState.INVALID_ID)

        account = self._account()
        if not account:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        if _expected_account is not None and account != _expected_account:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        if _expected_account is not None:
            record = self._store.get(account, cid)
            if record is None or record["state"] != "pending":
                state = {"completed": CleanupState.ALREADY_COMPLETED, "retained": CleanupState.RETAINED}.get(
                    record["state"] if record else "", CleanupState.NOT_APPLICABLE
                )
                return CleanupObservation(state=state, upstream_chat_id=cid, idempotent=True)

        with self._lock:
            task = self._inflight_cleanup.get(cid)
            joined_existing = task is not None
            if task is None:
                task = asyncio.create_task(
                    self._execute_delete(
                        cid,
                        client=client,
                        client_initializer=client_initializer,
                        account=account,
                        force=_expected_account is None,
                    )
                )
                self._inflight_cleanup[cid] = task

        try:
            observation = await task
        finally:
            with self._lock:
                if self._inflight_cleanup.get(cid) is task and task.done():
                    self._inflight_cleanup.pop(cid, None)

        if joined_existing:
            if observation.state is CleanupState.COMPLETED:
                return replace(
                    observation,
                    state=CleanupState.ALREADY_COMPLETED,
                    idempotent=True,
                )
            return replace(observation, idempotent=True)
        return observation

    async def recheck_cleanup_result(self, cid: str, *, client: Any) -> CleanupObservation:
        """Freshly verify a known owned record; never select targets by title."""
        from .services.history import observe_chat_absence

        account = self._account()
        record = self._store.get(account, cid) if account else None
        if record is None:
            return CleanupObservation(state=CleanupState.NOT_APPLICABLE, upstream_chat_id=cid)
        if record["state"] == "retained":
            return CleanupObservation(state=CleanupState.RETAINED, upstream_chat_id=cid)
        assert account is not None
        deferred = self._defer_if_protected(account, cid, source=record["source"])
        if deferred is not None:
            return replace(deferred, state=CleanupState.PENDING)
        try:
            absent, _details = await observe_chat_absence(client, cid)
        except Exception:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        if self._account_key_provider() != account:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        deferred = self._defer_if_protected(account, cid, source=record["source"])
        if deferred is not None:
            return replace(deferred, state=CleanupState.PENDING)
        if absent is True:
            updated = self._store.record_verified_absence(account, cid)
            current = self._store.get(account, cid)
            if not updated or current is None or current["state"] != "completed":
                state = CleanupState.RETAINED if current and current["state"] == "retained" else CleanupState.PENDING
                return CleanupObservation(state=state, upstream_chat_id=cid)
            if self._account_key_provider() != account:
                return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
            observation = CleanupObservation(state=CleanupState.ALREADY_COMPLETED, upstream_chat_id=cid, idempotent=True)
            with self._lock:
                if self._active_account == account:
                    self._completed_cleanup[cid] = observation
            return observation
        if absent is not False:
            return CleanupObservation(state=CleanupState.FAILED, upstream_chat_id=cid)
        if record["state"] == "completed":
            self._store.reopen_verified_present(account, cid)
            with self._lock:
                self._completed_cleanup.pop(cid, None)
                self._cleanup_observations.pop(cid, None)
        return await self.delete_chat_result(cid, client=client, _expected_account=account)

    async def _execute_delete(
        self,
        cid: str,
        *,
        client: Any,
        client_initializer: Callable[[], Any] | None,
        account: str,
        force: bool = True,
    ) -> CleanupObservation:
        with self._lock:
            pending = self._pending_cleanup.get(cid)
            source = pending.source if pending is not None else ""
            attempts = (pending.attempts if pending is not None else 0) + 1

        if force:
            self._store.schedule(account, cid, time.time(), source, retain=False)
            self._store.expedite_pending(account, cid)
        record = self._store.get(account, cid)
        if record and record["state"] in {"retained", "completed"}:
            state = CleanupState.RETAINED if record["state"] == "retained" else CleanupState.ALREADY_COMPLETED
            return CleanupObservation(state=state, upstream_chat_id=cid, idempotent=True, source=source)
        deferred = self._defer_if_protected(account, cid, source=source)
        if deferred is not None:
            return deferred
        token = self._store.claim(account, cid, force=force)
        if token is None:
            record = self._store.get(account, cid)
            state = {"completed": CleanupState.ALREADY_COMPLETED, "retained": CleanupState.RETAINED}.get(
                record["state"] if record else "", CleanupState.PENDING
            )
            return CleanupObservation(state=state, upstream_chat_id=cid, idempotent=True, source=source)

        claimed = self._store.get(account, cid)
        if claimed is not None:
            attempts = claimed["attempts"]
            source = claimed["source"]

        async def renew() -> None:
            while True:
                await asyncio.sleep(60)
                if not self._store.renew(account, cid, token):
                    return

        renew_task = asyncio.create_task(renew())
        try:
            if client is None:
                if client_initializer is not None:
                    client = client_initializer()
                    if inspect.isawaitable(client):
                        client = await client
                elif self._client_provider is not None:
                    client = self._client_provider()
            if self._account_key_provider() != account:
                raise RuntimeError("Cleanup account changed before execution")
            current = self._store.get(account, cid)
            if current is None or current["state"] != "pending" or current["lease_token"] != token:
                return CleanupObservation(state=CleanupState.RETAINED, upstream_chat_id=cid, source=source)
            # Import lazily to avoid service/client-manager import cycles.
            from .services.history import delete_chat_result as delete_with_readback
            from .services.history import observe_chat_absence

            already_absent = False
            if attempts > 1:
                absent, _details = await observe_chat_absence(client, cid)
                if absent is None:
                    raise RuntimeError("Prior cleanup outcome cannot be verified")
                already_absent = absent
            if not already_absent:
                # A retain request may have arrived while read-back was in flight.
                current = self._store.get(account, cid)
                if current is None or current["state"] != "pending" or current["lease_token"] != token:
                    return CleanupObservation(state=CleanupState.RETAINED, upstream_chat_id=cid, source=source)
                if self._account_key_provider() != account:
                    raise RuntimeError("Cleanup account changed before execution")
                deferred = self._defer_if_protected(account, cid, source=source, token=token)
                if deferred is not None:
                    return deferred
                result = await delete_with_readback(client, cid, expected_account=account,
                                                    account_key_provider=self._account_key_provider)
                if result.meta.verification_status != "verified_absent":
                    raise RuntimeError("Cleanup deletion was not verified")
            if self._account_key_provider() != account:
                raise RuntimeError("Cleanup account changed during read-back")
        except asyncio.CancelledError:
            self._store.finish(account, cid, token, verified=False, diagnostic_id=None)
            raise
        except Exception as error:  # noqa: BLE001 - persist arbitrary upstream failure evidence
            failure = self._record_failure(
                cid,
                attempts=attempts,
                source=source,
                error=error,
                account=account,
            )
            self._store.finish(account, cid, token, verified=False, diagnostic_id=failure.diagnostic_id)
            return failure
        finally:
            renew_task.cancel()
            try:
                await renew_task
            except asyncio.CancelledError:
                pass

        self._store.finish(account, cid, token, verified=True, diagnostic_id=None)
        current = self._store.get(account, cid)
        if current is None or current["state"] != "completed":
            return CleanupObservation(state=CleanupState.PENDING, upstream_chat_id=cid, source=source)

        observation = CleanupObservation(
            state=CleanupState.COMPLETED,
            upstream_chat_id=cid,
            attempts=attempts,
            source=source,
        )
        with self._lock:
            if self._active_account == account and self._account_key_provider() == account:
                self._pending_cleanup.pop(cid, None)
                self._completed_cleanup[cid] = observation
                self._cleanup_observations[cid] = observation

        logger.info("已删除远端 Gemini 对话: %s", cid)
        return observation

    def _defer_if_protected(
        self, account: str, cid: str, *, source: str, token: str | None = None,
    ) -> CleanupObservation | None:
        try:
            protected = self._cleanup_guard(cid)
        except Exception as error:
            logger.warning("Cleanup protection could not be checked error_type=%s", type(error).__name__)
            protected = True
        if not protected:
            return None
        self._store.defer_protected(account, cid, token=token)
        record = self._store.get(account, cid)
        state = {"retained": CleanupState.RETAINED, "completed": CleanupState.ALREADY_COMPLETED}.get(
            record["state"] if record else "", CleanupState.PENDING
        )
        return CleanupObservation(
            state=state, upstream_chat_id=cid, source=source,
            attempts=record["attempts"] if record else 0,
            delete_at=record["delete_at"] if record else None,
        )

    def _record_failure(
        self,
        cid: str,
        *,
        attempts: int,
        source: str,
        error: BaseException,
        account: str | None = None,
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
            if self._active_account == account and self._account_key_provider() == account:
                pending = self._pending_cleanup.get(cid)
                if pending is None:
                    pending = CleanupTask(delete_at=time.time(), source=source)
                    self._pending_cleanup[cid] = pending
                pending.attempts = attempts
                pending.last_diagnostic_id = diagnostic_id
                self._cleanup_observations[cid] = observation
        logger.warning(
            "删除远端 Gemini 对话失败 cid=%s diagnostic_id=%s error_type=%s",
            cid,
            diagnostic_id,
            type(error).__name__,
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
        account = self._account()
        with self._lock:
            pending = self._pending_cleanup.get(cid)
            attempts = (pending.attempts if pending is not None else 0) + 1
        failure = self._record_failure(
            cid,
            attempts=attempts,
            source=source,
            error=error,
            account=account,
        )
        if account:
            self._store.schedule(account, cid, time.time(), source, retain=False)
            assert failure.diagnostic_id is not None
            self._store.record_failure(account, cid, failure.diagnostic_id)
        return failure

    async def cleanup_due_chats(
        self,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
    ) -> int:
        """清理已经到期的远端 Gemini chat。"""
        results = await self.cleanup_due_chat_results(
            client=client,
            client_initializer=client_initializer,
        )
        return sum(result.state is CleanupState.COMPLETED for result in results)

    async def cleanup_due_chat_results(
        self,
        client: Any = None,
        client_initializer: Callable[[], Any] | None = None,
    ) -> tuple[CleanupObservation, ...]:
        """Clean due chats and retain a diagnosable result for every attempt."""
        account = self._account()
        if not account:
            return ()
        now = time.time()
        due_cids = [cid for cid, data in self.list_pending_cleanup().items() if data.delete_at <= now]

        if not due_cids:
            return ()

        if client is None:
            if client_initializer is not None:
                client = client_initializer()
                if inspect.isawaitable(client):
                    client = await client
            elif self._client_provider is not None:
                client = self._client_provider()

        results = []
        for cid in due_cids:
            if self._account_key_provider() != account:
                break
            results.append(await self.delete_chat_result(cid, client=client, _expected_account=account))
        return tuple(results)

    def list_pending_cleanup(self) -> dict[str, CleanupTask]:
        """返回待自动删除的远端 chat。"""
        account = self._account()
        if not account:
            return {}
        return {
            row["chat_id"]: CleanupTask(
                delete_at=row["delete_at"], source=row["source"], attempts=row["attempts"],
                last_diagnostic_id=row["diagnostic_id"],
            )
            for row in self._store.pending(account)
        }

    def get_cleanup_observation(
        self,
        cid: str | None,
    ) -> CleanupObservation | None:
        """Return the latest observable result without exposing raw failures."""
        if not is_valid_remote_chat_id(cid):
            return None
        account = self._account()
        if not account:
            return None
        record = self._store.get(account, cid)
        if record:
            states = {"pending": CleanupState.FAILED if record["diagnostic_id"] else CleanupState.PENDING,
                      "retained": CleanupState.RETAINED,
                      "completed": CleanupState.COMPLETED}
            return CleanupObservation(
                state=states[record["state"]], upstream_chat_id=cid, attempts=record["attempts"],
                diagnostic_id=record["diagnostic_id"], source=record["source"], delete_at=record["delete_at"],
            )
        with self._lock:
            return self._cleanup_observations.get(cid)

    def list_cleanup_observations(self) -> dict[str, CleanupObservation]:
        """Return a snapshot of pending, completed, retained, and failed states."""
        account = self._account()
        if not account:
            return {}
        result: dict[str, CleanupObservation] = {}
        for row in self._store.all_records(account):
            observation = self.get_cleanup_observation(row["chat_id"])
            if observation is not None:
                result[row["chat_id"]] = observation
        return result
