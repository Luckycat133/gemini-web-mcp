"""Detached background execution; request content lives only in an anonymous pipe and RAM."""

from __future__ import annotations

import asyncio
from asyncio import current_task
import json
import logging
import os
import random
import sys
import time
from typing import Any

from .infrastructure.account_identity import current_account_key, operation_account_matches
from .infrastructure.operation_store import OperationRecord, OperationStore
from .services.media_cleanup import has_verified_owned_cleanup, reconcile_owned_media_cleanup

_MEDIA_RATE_LIMIT_BACKOFF_SECONDS = 5 * 60
_MEDIA_RATE_LIMIT_MAX_BACKOFF_SECONDS = 30 * 60
_STATIC_FAILURE_PHASES = frozenset({
    "media_readback_error",
    "media_identity_unverified",
})
_MAX_STATIC_FAILURE_POLLS = 3
_MAX_MEDIA_ARTIFACT_POLLS = {
    "image": 4,
    "music": 12,
    "video": 15,
}
_DEFAULT_MAX_MEDIA_ARTIFACT_POLLS = 6


def _static_partial_poll_limit(record: OperationRecord) -> int | None:
    if record.state != "partial" or record.error_code == "RATE_LIMITED":
        return None
    if record.phase == "media_artifact_not_observed":
        return _MAX_MEDIA_ARTIFACT_POLLS.get(record.media_type or "", _DEFAULT_MAX_MEDIA_ARTIFACT_POLLS)
    if record.phase in _STATIC_FAILURE_PHASES:
        return _MAX_STATIC_FAILURE_POLLS
    return None


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
    kind = payload["kind"]
    omni_job = kind in {"omni_video", "omni_video_resume"}
    reconciled_media_cleanup = False
    exhausted_static_partial = False
    try:
        if omni_job:
            from .services.omni_video_jobs import run_omni_video_job
            await run_omni_video_job(
                store, operation_id, token, request=payload.pop("request", {}) if kind == "omni_video" else None,
                observation_seconds=observation_seconds, poll_seconds=poll_seconds,
            )
            return
        record = store.get(operation_id)
        if kind == "media_resume" and record is not None and reconcile_owned_media_cleanup(store, record):
            reconciled_media_cleanup = True
            return
        cookies = payload.pop("auth_cookies", None)
        auth_source = payload.pop("auth_source", None)
        if isinstance(cookies, dict) and cookies.get("__Secure-1PSID"):
            os.environ["GEMINI_PSID"] = cookies["__Secure-1PSID"]
            if cookies.get("__Secure-1PSIDTS"):
                os.environ["GEMINI_PSIDTS"] = cookies["__Secure-1PSIDTS"]
        from .client_wrapper import init_startup_infrastructure
        init_startup_infrastructure()
        if isinstance(cookies, dict) and cookies.get("__Secure-1PSID"):
            from .cookie_manager import get_cookie_manager
            get_cookie_manager().update_cookie(
                cookies["__Secure-1PSID"], cookies.get("__Secure-1PSIDTS", ""),
                extra_cookies=cookies,
                source=("browser_worker" if auth_source == "browser_worker" else "worker_inherited"),
                notify=False,
            )
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
        if kind == "media_resume" and reconcile_owned_media_cleanup(store, record):
            reconciled_media_cleanup = True
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
        elif kind != "media_resume":
            raise ValueError("unsupported background job")
        delay = 0.0 if kind == "media_resume" else poll_seconds
        rate_limit_backoff = _MEDIA_RATE_LIMIT_BACKOFF_SECONDS
        static_partial_polls = 0
        while store.is_worker_owner(operation_id, token):
            record = store.get(operation_id)
            if record is None or not operation_account_matches(record):
                break
            if (record.state not in {"completed", "failed", "unavailable", "cancelled"}
                    or record.cleanup_state not in {"completed", "already_completed"}):
                if reconcile_owned_media_cleanup(store, record):
                    reconciled_media_cleanup = True
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
            if record.state == "partial" and record.phase == "media_identity_incomplete":
                break
            poll_limit = _static_partial_poll_limit(record)
            if poll_limit is not None and static_partial_polls >= poll_limit:
                exhausted_static_partial = True
                break
            remaining = observation_seconds - (time.monotonic() - started)
            if not record.upstream_chat_id or remaining <= 0:
                break
            if record.error_code == "RATE_LIMITED":
                delay = max(poll_seconds, rate_limit_backoff)
                if poll_seconds >= 1.0 and not os.environ.get("PYTEST_CURRENT_TEST"):
                    jitter = random.uniform(5.0, 25.0)
                    if delay + jitter < remaining:
                        delay += jitter
                rate_limit_backoff = min(2 * rate_limit_backoff, _MEDIA_RATE_LIMIT_MAX_BACKOFF_SECONDS)
            else:
                rate_limit_backoff = _MEDIA_RATE_LIMIT_BACKOFF_SECONDS
            if delay >= remaining:
                break
            if delay:
                await asyncio.sleep(delay)
            remaining = observation_seconds - (time.monotonic() - started)
            record = store.get(operation_id)
            if (remaining <= 0 or record is None or not operation_account_matches(record)
                    or not store.is_worker_owner(operation_id, token) or not record.upstream_chat_id):
                break
            if reconcile_owned_media_cleanup(store, record):
                reconciled_media_cleanup = True
                break
            delay = poll_seconds
            if record.state in {"completed", "failed", "unavailable", "cancelled"}:
                continue
            try:
                await asyncio.wait_for(recover_media_job(record, store=store, worker_token=token), timeout=remaining)
            except TimeoutError:
                break
            after_recover = store.get(operation_id)
            if after_recover is not None and _static_partial_poll_limit(after_recover) is not None:
                static_partial_polls += 1
            else:
                static_partial_polls = 0
    except asyncio.CancelledError:
        raise
    except Exception:
        store.finish_worker(operation_id, worker_token=token, state="partial", phase="worker_outcome_unverified",
                            verification_status="not_observed", error_code="WORKER_INTERRUPTED")
    finally:
        pending_error = sys.exc_info()[1]
        finalization_cancelled: asyncio.CancelledError | None = None
        retirement_error: Exception | None = None
        cleanup_uncertain = False
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError as error:
            current = current_task()
            if pending_error is None and current is not None and current.cancelling():
                finalization_cancelled = error
        except Exception:
            cleanup_uncertain = True
        try:
            if kind in {"media", "media_resume"} and not store.is_worker_owner(operation_id, token):
                # A superseded observer must not drain another owner's work.
                cleanup_uncertain = True
                saved = store.get(operation_id)
                if isinstance(saved, OperationRecord) and has_verified_owned_cleanup(saved):
                    reconciled_media_cleanup = True
        except Exception:
            cleanup_uncertain = True
        try:
            store.retire_worker(operation_id, token)
        except Exception as error:
            retirement_error = error
            cleanup_uncertain = True
        # Only due, unprotected cleanup is eligible. Active operations remain protected.
        if not omni_job:
            try:
                if kind in {"media", "media_resume"}:
                    saved = store.get(operation_id)
                    if isinstance(saved, OperationRecord) and (
                        not saved.retain_chat
                        and saved.upstream_chat_id
                        and saved.cleanup_state not in {"completed", "already_completed"}
                        and saved.error_code != "RATE_LIMITED"
                        and (
                            saved.state in {"failed", "unavailable", "cancelled"}
                            or (
                                saved.state == "partial"
                                and (saved.phase == "media_identity_incomplete" or exhausted_static_partial)
                            )
                        )
                    ):
                        try:
                            store.release_cleanup(operation_id)
                            from .client_wrapper import finalize_remote_chat_cleanup
                            cleanup = await finalize_remote_chat_cleanup(
                                saved.upstream_chat_id,
                                retain_chat=False,
                                delete_after_seconds=saved.delete_after_seconds or 0,
                                source=f"{saved.operation}:{saved.state}",
                            )
                            store.update_cleanup(operation_id, cleanup.state.value)
                        except Exception:
                            # Cleanup errors must never mask worker shutdown.
                            pass
            except Exception:
                pass
            try:
                from .client_wrapper import _cleanup_manager, _drain_due_cleanup, _client_manager, schedule_remote_chat_cleanup
                try:
                    if (not cleanup_uncertain and not reconciled_media_cleanup
                            and pending_error is None and finalization_cancelled is None):
                        saved_media = store.get(operation_id) if kind in {"media", "media_resume"} else None
                        if saved_media is None or saved_media.error_code != "RATE_LIMITED":
                            try:
                                from .services.background import repair_operation_cleanup
                                repair_operation_cleanup(
                                    store, _cleanup_manager.get_cleanup_observation, schedule_remote_chat_cleanup,
                                )
                            except Exception:
                                pass
                            await asyncio.wait_for(_drain_due_cleanup(), 20)
                except asyncio.CancelledError as error:
                    finalization_cancelled = error
                except Exception:
                    pass
                finally:
                    try:
                        await asyncio.wait_for(_client_manager.reset_async(), 10)
                    except asyncio.CancelledError as error:
                        finalization_cancelled = error
                    except Exception:
                        pass
            except Exception:
                pass
        if pending_error is None:
            if finalization_cancelled is not None:
                raise finalization_cancelled
            if retirement_error is not None:
                raise retirement_error


def main() -> None:
    logging.disable(logging.CRITICAL)
    try:
        from .sdk_auth_logging import suppress_sdk_auth_logs
        suppress_sdk_auth_logs()
        payload = json.load(sys.stdin)
        if payload.get("kind") in {"omni_video", "omni_video_resume"}:
            asyncio.run(run_worker(payload))
        else:
            from .client_manager import isolated_worker_cookie_cache
            with isolated_worker_cookie_cache():
                asyncio.run(run_worker(payload))
    except Exception:
        # No prompt, raw response, credential, or exception text may enter a worker log.
        raise SystemExit(1)


if __name__ == "__main__":
    main()
