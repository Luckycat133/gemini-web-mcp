"""Detached background execution; request content lives only in an anonymous pipe and RAM."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

from .infrastructure.account_identity import current_account_key, operation_account_matches
from .infrastructure.operation_store import OperationStore


async def _heartbeat(store: OperationStore, operation_id: str, token: str) -> None:
    while store.heartbeat_worker(operation_id, token):
        await asyncio.sleep(10)


async def _wait_for_media_cleanup(store: OperationStore, operation_id: str, token: str,
                                  *, observation_seconds: float, poll_seconds: float) -> None:
    """Keep explicit delays and cleanup retries alive after the submitting client exits."""
    from .client_wrapper import _cleanup_manager, _drain_due_cleanup

    started = time.monotonic()
    while store.is_worker_owner(operation_id, token) and time.monotonic() - started < observation_seconds:
        record = store.get(operation_id)
        if (record is None or not operation_account_matches(record) or record.retain_chat
                or not record.upstream_chat_id or record.cleanup_protected
                or record.cleanup_state in {"completed", "already_completed", "retained"}):
            return
        observation = _cleanup_manager.get_cleanup_observation(record.upstream_chat_id)
        if observation is None:
            return
        store.update_cleanup(operation_id, observation.state.value)
        if observation.state.value in {"completed", "already_completed", "retained"}:
            return
        await _drain_due_cleanup()
        delay = max(0.05, min(poll_seconds, (observation.delete_at or time.time()) - time.time()))
        await asyncio.sleep(delay)


async def _research(request: dict[str, Any], operation_id: str, token: str, *, resume: bool) -> None:
    from .client_wrapper import (
        cleanup_due_remote_chats, finalize_remote_chat_cleanup, get_gemini_client,
        initialize_client, schedule_remote_chat_cleanup,
    )
    from .constants import resolve_model_name
    from .services.research import ResearchRequest, ResearchService, ResearchServiceDependencies

    if resume:
        record = OperationStore().get(operation_id)
        if record is None or record.phase != "plan_outcome_unverified":
            return
        request = dict(query="Resume the original research operation.", operation_id=operation_id,
                       operation=record.operation, model=record.requested_model,
                       thinking_level=record.thinking_level, retain_chat=record.retain_chat,
                       delete_after_seconds=record.delete_after_seconds)
    service = ResearchService(ResearchServiceDependencies(
        client_provider=get_gemini_client, client_initializer=initialize_client,
        cleanup_due_remote_chats=cleanup_due_remote_chats, schedule_chat_cleanup=schedule_remote_chat_cleanup,
        resolve_model=resolve_model_name, finalize_chat_cleanup=finalize_remote_chat_cleanup,
    ))
    await service.start(ResearchRequest(**request), worker_token=token)


async def run_worker(payload: dict[str, Any], *, store: OperationStore | None = None,
                     observation_seconds: float = 4 * 60 * 60, poll_seconds: float = 30) -> None:
    store = store or OperationStore()
    operation_id, token = payload["operation_id"], payload["worker_token"]
    if not store.is_worker_owner(operation_id, token):
        return
    store.bind_worker(operation_id, token, os.getpid())
    heartbeat = asyncio.create_task(_heartbeat(store, operation_id, token))
    try:
        from .client_wrapper import init_startup_infrastructure
        init_startup_infrastructure()
        cookies = payload.pop("auth_cookies", None)
        if isinstance(cookies, dict) and cookies.get("__Secure-1PSID"):
            from .cookie_manager import get_cookie_manager
            get_cookie_manager().update_cookie(
                cookies["__Secure-1PSID"], cookies.get("__Secure-1PSIDTS", ""),
                extra_cookies=cookies, source=("browser_worker" if payload.pop("auth_source", None) == "browser_worker"
                                              else "worker_inherited"),
            )
        kind = payload["kind"]
        record = store.get(operation_id)
        account = current_account_key()
        if not account:
            store.finish_worker(operation_id, worker_token=token, state="partial", phase="account_identity_unavailable",
                                verification_status="account_mismatch_or_unbound", error_code="AUTH_REQUIRED")
            return
        if (record is not None and record.account_binding_pending and account and kind in {"media", "research", "task", "notebook_chat"}):
            store.bind_pending_account(operation_id, worker_token=token, account_key=account)
            record = store.get(operation_id)
        if record is None or not operation_account_matches(record):
            return
        if kind in {"task", "task_resume", "notebook_chat", "task_input"}:
            from .services.task_jobs import execute_task_job, recover_task_job
            started = time.monotonic()
            if kind in {"task", "notebook_chat", "task_input"}:
                await execute_task_job(payload.pop("request", {}), operation_id=operation_id,
                                       store=store, worker_token=token)
            while store.is_worker_owner(operation_id, token) and time.monotonic() - started < observation_seconds:
                record = store.get(operation_id)
                if record is None or not operation_account_matches(record):
                    break
                if record.state in {"completed", "failed"} and record.cleanup_protected:
                    # Save the full verified answer and release cleanup without
                    # requiring a caller to request the result first.
                    from .client_wrapper import get_gemini_client, initialize_client, finalize_remote_chat_cleanup
                    from .services.operations import OperationService
                    await OperationService(store, get_gemini_client, initialize_client,
                                           finalize_remote_chat_cleanup).get_result(operation_id)
                    record = store.get(operation_id)
                    if record is None or not operation_account_matches(record):
                        break
                if not record.cleanup_protected:
                    await _wait_for_media_cleanup(store, operation_id, token,
                                                  observation_seconds=observation_seconds, poll_seconds=poll_seconds)
                    break
                retry_failed_answer = record.state == 'failed' and record.phase in {'task_failed', 'task_failure_saved'} and record.cleanup_protected
                if (record.state in {"failed", "unavailable", "cancelled"} and not retry_failed_answer) or not record.upstream_chat_id:
                    break
                await asyncio.sleep(poll_seconds)
                if record.state != "completed":
                    await recover_task_job(record, store=store, worker_token=token)
            return
        if kind in {"research", "research_resume", "research_observe"}:
            if kind != "research_observe":
                await _research(payload.get("request", {}), operation_id, token, resume=kind == "research_resume")
            payload.pop("request", None)
            from .client_wrapper import get_gemini_client, initialize_client, finalize_remote_chat_cleanup
            from .services.operations import OperationService
            reader = OperationService(store, get_gemini_client, initialize_client,
                                      finalize_remote_chat_cleanup, worker_token=token)
            started = time.monotonic()
            while store.is_worker_owner(operation_id, token) and time.monotonic() - started < observation_seconds:
                record = store.get(operation_id)
                if (record is None or not operation_account_matches(record) or not record.upstream_chat_id
                        or record.state in {"failed", "unavailable", "cancelled"}):
                    break
                if record.cleanup_state in {"completed", "already_completed", "retained"}:
                    break
                remaining = observation_seconds - (time.monotonic() - started)
                # A resumed observer only reads its original CID. It never
                # replays the query or independently confirms a pending plan.
                if (kind != "research_observe" and record.phase == "plan_outcome_unverified"
                        and record.cleanup_protected and record.requested_model and record.thinking_level):
                    await asyncio.sleep(min(poll_seconds, remaining))
                    await asyncio.wait_for(_research({}, operation_id, token, resume=True),
                                           timeout=max(0.01, observation_seconds - (time.monotonic() - started)))
                    continue
                try:
                    await asyncio.wait_for(reader.get_result(operation_id), timeout=max(0.01, remaining))
                except TimeoutError:
                    break
                record = store.get(operation_id)
                if record is None or not operation_account_matches(record) or not store.is_worker_owner(operation_id, token):
                    break
                if not record.cleanup_protected:
                    await _wait_for_media_cleanup(store, operation_id, token,
                        observation_seconds=max(0, observation_seconds - (time.monotonic() - started)),
                        poll_seconds=poll_seconds)
                    break
                if record.state == "completed" and record.retain_chat:
                    break
                await asyncio.sleep(min(poll_seconds, max(0, observation_seconds - (time.monotonic() - started))))
            return
        from .services.media_jobs import execute_media_job, recover_media_job
        started = time.monotonic()
        if kind == "media":
            await execute_media_job(payload["request"], operation_id=operation_id, store=store, worker_token=token)
            payload.pop("request", None)
        elif kind == "media_resume":
            record = store.get(operation_id)
            if record is not None and record.state != "completed":
                await recover_media_job(record, store=store, worker_token=token)
        else:
            raise ValueError("unsupported background job")
        while store.is_worker_owner(operation_id, token):
            record = store.get(operation_id)
            if record is None:
                break
            if record.state == "completed":
                if not record.retain_chat and record.cleanup_state not in {"completed", "already_completed"}:
                    from .client_wrapper import get_gemini_client, initialize_client, finalize_remote_chat_cleanup
                    from .services.operations import OperationService
                    await OperationService(store, get_gemini_client, initialize_client,
                                           finalize_remote_chat_cleanup).get_result(operation_id)
                await _wait_for_media_cleanup(store, operation_id, token,
                                              observation_seconds=observation_seconds, poll_seconds=poll_seconds)
                break
            if record.state in {"failed", "unavailable", "cancelled"}:
                await _wait_for_media_cleanup(store, operation_id, token,
                                              observation_seconds=observation_seconds, poll_seconds=poll_seconds)
                break
            if not record.upstream_chat_id or time.monotonic() - started >= observation_seconds:
                break
            await asyncio.sleep(poll_seconds)
            await recover_media_job(record, store=store, worker_token=token)
    except asyncio.CancelledError:
        raise
    except Exception:
        store.finish_worker(operation_id, worker_token=token, state="partial", phase="worker_outcome_unverified",
                            verification_status="not_observed", error_code="WORKER_INTERRUPTED")
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        store.retire_worker(operation_id, token)
        # Only due, unprotected cleanup is eligible. Active operations remain protected.
        try:
            from .client_wrapper import _drain_due_cleanup, _client_manager
            await asyncio.wait_for(_drain_due_cleanup(), 20)
            await asyncio.wait_for(_client_manager.reset_async(), 10)
        except Exception:
            pass


def main() -> None:
    logging.disable(logging.CRITICAL)
    try:
        from .sdk_auth_logging import suppress_sdk_auth_logs
        suppress_sdk_auth_logs()
        payload = json.load(sys.stdin)
        asyncio.run(run_worker(payload))
    except Exception:
        # No prompt, raw response, credential, or exception text may enter a worker log.
        raise SystemExit(1)


if __name__ == "__main__":
    main()
