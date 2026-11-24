"""Metadata-only background media execution and identity-bound recovery."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, asynccontextmanager
from dataclasses import replace
import fcntl
import logging
import os
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Any

from ..constants import (
    is_generation_failure_reply, is_image_tool_unavailable_reply, is_music_notation_reply,
    is_music_tool_unavailable_reply, resolve_media_request,
)
from ..client_manager import ClientAuthenticationRequired
from ..domain import ArtifactKind, ArtifactState, result_from_exception
from ..domain.results import classify_exception
from ..infrastructure.account_identity import current_account_key, operation_account_matches
from ..infrastructure.rpc_contracts import (
    VideoTransportChanged, WebWorkflowTransportChanged, read_completed_empty_media_turn, read_completed_music_failure_turn,
)
from ..tools.utils import validate_image_paths
from .artifacts import is_response_queued, observed_backend_from_response, response_chat_id
from .client_timeouts import client_readback_scope
from .deadlines import await_before_deadline
from .media_generation import (
    VideoModeUnavailable, compose_media_prompt, generate_media_response, media_timeout, media_timeout_scope,
    music_option_arguments, music_options_error,
)
from .media_inputs import MediaSourceUnavailable, source_image_upload
from .media_cleanup import reconcile_owned_media_cleanup
from .media_storage import ImageDecoderUnavailable, _save_generated_media, _valid_media_filename, require_image_decoder
from .model_policy import model_selection_from_response
from .music_recovery import MusicErrorCardObserved, fetch_music_media_from_chat, music_response_identity
from .native_media_generation import NativeMediaGenerationError, native_media_enabled, read_native_media_response
from .web_context_transport import fetch_workflow_turn

MEDIA_READBACK_TIMEOUT_SECONDS = 60.0
MEDIA_READBACK_MAX_ATTEMPTS = 3
MEDIA_READBACK_RETRY_DELAYS = (1.0, 2.0)
logger = logging.getLogger(__name__)

_QUOTA_REJECTION = "I can create more images as soon as your limit resets. Check your usage in Settings."
_VIDEO_QUOTA_REJECTION = "I can create more videos as soon as your limit resets. Check your usage in Settings."
_VIDEO_CAPACITY_REJECTION = (
    "I couldn't do that because I'm getting a lot of requests right now. Please try again later."
)
_IMAGE_CHAT_REFUSAL = "我是一个语言模型，这超出了我的设计用途。"
_VIDEO_REFUSAL = "请恕我爱莫能助，我只是一个语言模型，不能理解或回复你的这个问题。"
_MUSIC_ERROR_NOTICE = "生成这段钢琴和弦乐纯音乐时出现了一些问题。你要不要现在再重新尝试一次？"
_MUSIC_REFUSAL_ABC = ("I cannot render or stream playable audio files (such as MP3 or WAV) directly within this text "
                      "interface, but I can provide the complete original composition in standard ABC notation and a "
                      "playable script so you can hear ")
_MUSIC_QUOTA_PREFIX = "很抱歉，目前的音乐生成额度已达上限，暂时无法为你生成"
_MUSIC_REFUSAL = ("I'm unable to directly generate or output audio files here, as I don't have a built-in "
                  "direct audio synthesis tool attached to this chat interface.")
_MUSIC_NO_TOOL_PREFIX = "I don't have access to an audio or music generation tool in this session,"
_MUSIC_NATIVE_NO_TOOL_PREFIX = "I don't have a built-in audio generation tool available right now to render a playable sound file directly"
_MUSIC_NATIVE_SESSION_REFUSAL = "I can't generate or output an audio file directly right now because the music generation tool isn't available in this session."
_REJECTIONS = {"UPSTREAM_LIMIT_EXCEEDED": "upstream_limit_message_observed",
               "ARTIFACT_NOT_RETURNED": "model_media_refusal_observed",
               "UPSTREAM_REJECTED": "upstream_media_error_card_observed"}


@asynccontextmanager
async def _media_account_lock(store: Any, *, account_key: str | None, media_type: str):
    """Serialize per-account video/music slots so concurrent calls do not collide upstream."""
    store_path = getattr(store, "path", None)
    if media_type not in {"image", "video", "music"} or not account_key or not isinstance(store_path, (str, Path)):
        yield
        return
    lock_dir = Path(store_path).resolve().parent / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"media_{re.sub(r'[^a-zA-Z0-9_.-]', '_', account_key)}_{media_type}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                await asyncio.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


class _LostMediaLease(RuntimeError):
    pass


def _checkpoint(store: Any, operation_id: str, token: str, response: Any) -> None:
    record = store.get(operation_id) if hasattr(store, "get") else None
    account_changed = record is not None and not operation_account_matches(record)
    cid = response_chat_id(response) or ""
    metadata: Any = getattr(response, "metadata", ())
    rid = metadata[1] if isinstance(metadata, (list, tuple)) and len(metadata) > 1 else ""
    try:
        rcid = getattr(response, "rcid", "")
    except (IndexError, AttributeError):
        rcid = ""
    if not rcid and isinstance(metadata, (list, tuple)) and len(metadata) > 2:
        rcid = metadata[2]
    selection = model_selection_from_response(response)
    selected: dict[str, Any] = {"selected_model": selection.effective_model} if selection is not None else {}
    if getattr(response, "_mcp_native_media", False) is True:
        selected["native_submission"] = True
        if account_changed:
            checkpointed = bool(cid and store.checkpoint_media(
                operation_id, worker_token=token, chat_id=cid, native_submission=True,
            ))
            attach = getattr(store, "attach_late_chat", None)
            if cid and not checkpointed and callable(attach):
                try:
                    attach(operation_id, cid)
                except Exception:
                    pass
            raise _LostMediaLease()
    if cid and not store.checkpoint_media(
        operation_id, worker_token=token, chat_id=cid, response_id=rid or None, candidate_id=rcid or None,
        **selected,
    ):
        # A replacement worker may have taken the lease while the original
        # SDK request was still receiving its first CID. Save only the opaque
        # ID under the existing protected operation; never schedule under a
        # different account or replace a conflicting identity.
        attach = getattr(store, "attach_late_chat", None)
        if callable(attach):
            try:
                attach(operation_id, cid)
            except Exception:
                pass
        raise _LostMediaLease()
    if account_changed:
        # Preserve the old client's observed CID under its original account,
        # but never schedule deletion under the newly selected account.
        raise _LostMediaLease()


def _unknown(
    store: Any, operation_id: str, token: str, *, code: str | None = None, queued: bool = False,
    phase: str = "media_outcome_unverified",
) -> None:
    current = store.get(operation_id) if hasattr(store, "get") else None
    if (current is not None and getattr(current, "phase", None) == "native_media_submitted"
            and current.upstream_chat_id and not current.upstream_response_id):
        phase = "native_media_submitted"
    store.finish_worker(operation_id, worker_token=token, state="queued" if queued else "partial", phase=phase,
                        verification_status="not_observed", error_code=code)


def _rate_limited_read(error: Exception, record: Any, *, store: Any, token: str) -> bool:
    """Preserve throttling so the original worker can back off without releasing cleanup."""
    if classify_exception(error).error_code != "RATE_LIMITED":
        return False
    if operation_account_matches(record) and store.is_worker_owner(record.operation_id, token):
        _unknown(store, record.operation_id, token, phase="media_readback_rate_limited", code="RATE_LIMITED")
    return True


def _matching_response(history: Any, cid: str, rid: str, rcid: str) -> Any | None:
    for turn in getattr(history, "turns", ()) or ():
        response = getattr(turn, "model_output", None)
        if getattr(turn, "role", None) != "model" or response is None:
            continue
        metadata: Any = getattr(response, "metadata", ())
        if len(metadata) < 2 or metadata[0] != cid or metadata[1] != rid:
            continue
        for index, candidate in enumerate(getattr(response, "candidates", ()) or ()):
            if getattr(candidate, "rcid", None) != rcid:
                continue
            copy = getattr(response, "model_copy", None)
            if callable(copy):
                return copy(update={"chosen": index})
            return SimpleNamespace(metadata=metadata, candidates=response.candidates, chosen=index, rcid=rcid,
                                   text=getattr(candidate, "text", ""),
                                   images=getattr(candidate, "generated_images", ()),
                                   videos=getattr(candidate, "generated_videos", ()),
                                   media=getattr(candidate, "generated_media", ()))
    return None


def _recover_exact_chat_response(history: Any, cid: str, expected_rid: str | None = None) -> Any | None:
    """Recover a single unambiguous turn (rid, rcid) from read_chat when checkpointing lagged."""
    outputs = []
    for turn in getattr(history, "turns", ()) or ():
        response = getattr(turn, "model_output", None)
        if getattr(turn, "role", None) != "model" or response is None:
            continue
        metadata: Any = getattr(response, "metadata", ())
        candidates = getattr(response, "candidates", ()) or ()
        if (not isinstance(metadata, (list, tuple)) or len(metadata) < 2 or metadata[0] != cid
                or not isinstance(metadata[1], str) or not metadata[1].startswith("r_")
                or (expected_rid and metadata[1] != expected_rid)
                or not isinstance(candidates, (list, tuple)) or len(candidates) != 1):
            continue
        rcid = getattr(candidates[0], "rcid", None)
        if not isinstance(rcid, str) or not rcid.startswith("rc_"):
            continue
        if len(metadata) > 2 and metadata[2] and metadata[2] != rcid:
            continue
        copy = getattr(response, "model_copy", None)
        if callable(copy):
            outputs.append(copy(update={"chosen": 0}))
        else:
            outputs.append(SimpleNamespace(
                metadata=metadata,
                candidates=candidates,
                chosen=0,
                rcid=rcid,
                text=getattr(candidates[0], "text", getattr(response, "text", "")),
                images=getattr(candidates[0], "generated_images", getattr(response, "images", ())),
                videos=getattr(candidates[0], "generated_videos", getattr(response, "videos", ())),
                media=getattr(candidates[0], "generated_media", getattr(response, "media", ())),
            ))
    return outputs[0] if len(outputs) == 1 else None


async def _finish_media_rejection(
    client: Any, response: Any, record: Any, *, store: Any, token: str, code: str,
    verification_status: str | None = None,
) -> None:
    """Checkpoint the exact observed rejection before releasing cleanup protection."""
    if not operation_account_matches(record):
        return
    verification = ("upstream_media_failure_reply_observed"
                    if code == "UPSTREAM_REJECTED" and record.media_type == "image" else _REJECTIONS[code])
    if (code == "ARTIFACT_NOT_RETURNED" and record.media_type == "music"
            and verification_status == "completed_music_notation_without_artifact_observed"):
        verification = verification_status
    if not store.finish_worker(record.operation_id, worker_token=token, state="partial", phase="media_rejected",
                               verification_status=verification, error_code=code):
        return
    if not operation_account_matches(record) or not store.release_failed_media_cleanup(record.operation_id, worker_token=token):
        return
    from ..client_wrapper import finalize_remote_chat_cleanup_from_response

    try:
        cleanup = await finalize_remote_chat_cleanup_from_response(
            response, retain_chat=record.retain_chat, delete_after_seconds=record.delete_after_seconds,
            source=f"{record.operation}:rejected", client=client,
        )
        if store.is_worker_owner(record.operation_id, token) and operation_account_matches(record):
            store.update_cleanup(record.operation_id, cleanup.state.value)
    except Exception:
        pass  # The rejection checkpoint and durable cleanup queue remain available.
    if not operation_account_matches(record):
        return
    store.finish_worker(record.operation_id, worker_token=token, state="failed", phase="media_rejected",
                        verification_status=verification, error_code=code)


async def _save_response(
    client: Any, response: Any, record: Any, *, store: Any, token: str,
    readback_timeout: float = MEDIA_READBACK_TIMEOUT_SECONDS,
) -> None:
    if not operation_account_matches(record):
        return
    if is_response_queued(response):
        _unknown(store, record.operation_id, token, queued=True)
        return
    cid, rid, rcid = music_response_identity(response)
    if not cid or not rid or not rcid:
        has_media = any(getattr(response, name, None) for name in ("images", "videos", "media"))
        if has_media:
            get_fn = getattr(store, "get", None)
            fresh = (get_fn(record.operation_id) if callable(get_fn) else None) or record
            if not cid or cid == fresh.upstream_chat_id:
                cid = cid or fresh.upstream_chat_id
                rid = rid or fresh.upstream_response_id
                rcid = rcid or fresh.upstream_candidate_id
    if not cid or not rid or not rcid:
        _unknown(store, record.operation_id, token, phase="media_identity_incomplete")
        return
    text = getattr(response, "text", None)
    new_no_tool = record.media_type == "music" and is_music_tool_unavailable_reply(text)
    image_no_tool = record.media_type == "image" and is_image_tool_unavailable_reply(text)
    music_notation = record.media_type == "music" and is_music_notation_reply(text)
    generation_failure = is_generation_failure_reply(text)
    if (new_no_tool or image_no_tool or music_notation or generation_failure) and (cid, rid, rcid) != (
        record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id,
    ):
        _unknown(store, record.operation_id, token, phase="media_identity_unverified", code="READBACK_UNVERIFIED")
        return
    if (getattr(response, "text", None) in {_QUOTA_REJECTION, _VIDEO_QUOTA_REJECTION}
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
        await _finish_media_rejection(client, response, record, store=store, token=token, code="UPSTREAM_LIMIT_EXCEEDED")
        return
    if (record.media_type == "video" and getattr(response, "text", None) == _VIDEO_REFUSAL
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
        await _finish_media_rejection(client, response, record, store=store, token=token, code="ARTIFACT_NOT_RETURNED")
        return
    image_failure = getattr(response, "text", None)
    video_capacity = (record.media_type == "video" and isinstance(image_failure, str)
                      and " ".join(image_failure.split()) == _VIDEO_CAPACITY_REJECTION)
    if ((generation_failure
            or (record.media_type == "image" and image_failure == _IMAGE_CHAT_REFUSAL) or video_capacity)
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
        try:
            with client_readback_scope(client):
                if generation_failure and isinstance(image_failure, str):
                    notice = await await_before_deadline(read_completed_empty_media_turn(client, cid, rid, rcid, image_failure),
                                                         timeout=readback_timeout)
                    if notice.status == "rejected" and notice.reject_code == 429:
                        raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=cid)
                    verified_failure = notice.status == "success"
                    turn = None
                else:
                    verified_failure = False
                    turn = await await_before_deadline(fetch_workflow_turn(client, cid, rid, rcid), timeout=readback_timeout)
        except Exception as error:
            if _rate_limited_read(error, record, store=store, token=token):
                return
            verified_failure = False
            turn = None
        if generation_failure and not verified_failure:
            if operation_account_matches(record) and store.is_worker_owner(record.operation_id, token):
                _unknown(store, record.operation_id, token, phase="media_readback_error", code="READBACK_UNVERIFIED")
            return
        readback_text = turn.get("text") if turn else None
        same_failure = (readback_text == image_failure or (
            video_capacity and isinstance(readback_text, str)
            and " ".join(readback_text.split()) == _VIDEO_CAPACITY_REJECTION
        ))
        if (verified_failure or (turn and turn.get("completed") is True and same_failure
                and (turn.get("chat_id"), turn.get("rid"), turn.get("rcid")) == (cid, rid, rcid))):
            await _finish_media_rejection(client, response, record, store=store, token=token,
                                          code="UPSTREAM_LIMIT_EXCEEDED" if video_capacity else "UPSTREAM_REJECTED")
            return
    media_items = None
    if record.media_type == "music" and not getattr(response, "media", None):
        try:
            text = getattr(response, "text", None)
            requires_verified_empty = new_no_tool or music_notation or isinstance(text, str) and text.startswith(
                (_MUSIC_QUOTA_PREFIX, _MUSIC_REFUSAL, _MUSIC_REFUSAL_ABC, _MUSIC_NO_TOOL_PREFIX, _MUSIC_NATIVE_NO_TOOL_PREFIX, _MUSIC_NATIVE_SESSION_REFUSAL)
            ) or text == _MUSIC_ERROR_NOTICE
            read_cards = (fetch_music_media_from_chat(client, cid, rid=rid, rcid=rcid, strict=True)
                          if requires_verified_empty else fetch_music_media_from_chat(client, cid, rid=rid, rcid=rcid))
            with client_readback_scope(client):
                media_items = await await_before_deadline(read_cards, timeout=readback_timeout)
        except MusicErrorCardObserved:
            verified_no_tool_failure = False
            if (new_no_tool and isinstance(text, str)
                    and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
                try:
                    with client_readback_scope(client):
                        proof = await await_before_deadline(
                            read_completed_music_failure_turn(client, cid, rid, rcid, text), timeout=readback_timeout,
                        )
                    if proof.status == "rejected" and proof.reject_code == 429:
                        raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=cid)
                    verified_no_tool_failure = proof.status == "success"
                except Exception as error:
                    if _rate_limited_read(error, record, store=store, token=token):
                        return
            if ((text == _MUSIC_ERROR_NOTICE or verified_no_tool_failure)
                    and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
                await _finish_media_rejection(client, response, record, store=store, token=token,
                                              code="UPSTREAM_REJECTED")
            else:
                _unknown(store, record.operation_id, token, phase="media_readback_error", code="READBACK_UNVERIFIED")
            return
        except asyncio.TimeoutError:
            if operation_account_matches(record):
                _unknown(store, record.operation_id, token, phase="media_music_readback_timeout", code="TIMED_OUT")
            return
        except Exception as error:
            if _rate_limited_read(error, record, store=store, token=token):
                return
            _unknown(store, record.operation_id, token, phase="media_readback_error", code="READBACK_UNVERIFIED")
            return
    if not operation_account_matches(record):
        return
    text = getattr(response, "text", None)
    if ((new_no_tool or image_no_tool or music_notation) and isinstance(text, str) and not media_items
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
        try:
            with client_readback_scope(client):
                empty_turn = await await_before_deadline(read_completed_empty_media_turn(client, cid, rid, rcid, text),
                                                         timeout=readback_timeout)
            if empty_turn.status == "rejected" and empty_turn.reject_code == 429:
                raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=cid)
        except Exception as error:
            if _rate_limited_read(error, record, store=store, token=token):
                return
            empty_turn = None
        if empty_turn is None or empty_turn.status != "success":
            _unknown(store, record.operation_id, token, phase="media_readback_error", code="READBACK_UNVERIFIED")
            return
    if (image_no_tool and isinstance(text, str)
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
        await _finish_media_rejection(client, response, record, store=store, token=token, code="ARTIFACT_NOT_RETURNED")
        return
    if (record.media_type == "music" and isinstance(text, str) and text.startswith(_MUSIC_QUOTA_PREFIX)
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))
            and not media_items):
        await _finish_media_rejection(client, response, record, store=store, token=token, code="UPSTREAM_LIMIT_EXCEEDED")
        return
    if (record.media_type == "music" and isinstance(text, str)
            and (new_no_tool or music_notation or text.startswith((_MUSIC_REFUSAL, _MUSIC_REFUSAL_ABC, _MUSIC_NO_TOOL_PREFIX, _MUSIC_NATIVE_NO_TOOL_PREFIX, _MUSIC_NATIVE_SESSION_REFUSAL)))
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))
            and not media_items):
        await _finish_media_rejection(
            client, response, record, store=store, token=token, code="ARTIFACT_NOT_RETURNED",
            verification_status="completed_music_notation_without_artifact_observed" if music_notation else None,
        )
        return
    saved = await _save_generated_media(
        response, media_type=record.media_type, output_dir=record.output_dir,
        filename=record.filename or record.operation_id, prompt="", media_items=media_items,
        requested_model=record.requested_model, request_model=record.request_model,
        effective_backend=record.effective_backend, observed_backend=observed_backend_from_response(response),
        source_chat_id=cid,
    )
    if not operation_account_matches(record):
        return
    local = tuple(replace(item, uri=None, title=None) for item in saved.artifacts if item.state == ArtifactState.LOCAL)
    valid_kinds = {"image": {ArtifactKind.IMAGE}, "video": {ArtifactKind.VIDEO},
                   "music": {ArtifactKind.AUDIO, ArtifactKind.VIDEO}}
    if not any(item.kind in valid_kinds.get(record.media_type, set()) for item in local):
        _unknown(store, record.operation_id, token, phase="media_artifact_not_observed",
                 code="ARTIFACT_SAVE_FAILED" if saved.failures else getattr(response, "native_error_code", None))
        return
    if not store.save_media_artifacts(record.operation_id, local, worker_token=token, artifact_state="local"):
        return
    if not operation_account_matches(record) or not store.release_media_cleanup(record.operation_id, worker_token=token):
        return
    from ..client_wrapper import finalize_remote_chat_cleanup_from_response

    try:
        cleanup = await finalize_remote_chat_cleanup_from_response(
            response, retain_chat=record.retain_chat, delete_after_seconds=record.delete_after_seconds,
            source=f"{record.operation}:completed", client=client,
        )
    except Exception:
        # Local metadata is already durable and the cleanup queue remains available.
        return
    if store.is_worker_owner(record.operation_id, token) and operation_account_matches(record):
        store.update_cleanup(record.operation_id, cleanup.state.value)
        store.finish_worker(record.operation_id, worker_token=token, state="completed", phase="completed",
                            verification_status="local_artifact_verified")


async def execute_media_job(request: dict, *, operation_id: str, store: Any, worker_token: str) -> None:
    """Generate once; record turn identity as soon as the stream provides it."""
    from ..client_wrapper import get_gemini_client, initialize_client, schedule_remote_chat_cleanup_from_response

    media_type = request["media_type"]
    settings = resolve_media_request(request.get("model", "flash"), media_type, request.get("thinking_level", "standard"))
    filename = request.get("filename") or operation_id
    if (media_type not in {"image", "video", "music"} or not _valid_media_filename(filename)
            or music_options_error(
                media_type=media_type,
                music_duration=request.get("music_duration"),
                music_vocals=request.get("music_vocals"),
                music_genre=request.get("music_genre"),
            )):
        store.finish_worker(operation_id, worker_token=worker_token, state="failed", phase="input_rejected",
                            verification_status="input_rejected", error_code="INVALID_ARGUMENT")
        return
    record = store.get(operation_id) if hasattr(store, "get") else None
    if not store.is_worker_owner(operation_id, worker_token) or (record is not None and not operation_account_matches(record)):
        return
    store.configure_media(operation_id, media_type, request.get("output_dir"), filename,
                          settings["request_model"], settings["backend_label"], worker_token=worker_token)
    if media_type == "image":
        try:
            require_image_decoder()
        except ImageDecoderUnavailable:
            if store.finish_worker(operation_id, worker_token=worker_token, state="failed",
                                   phase="image_decoder_unavailable", verification_status="image_decoder_unavailable",
                                   error_code="CAPABILITY_UNAVAILABLE") and (record is None or not record.upstream_chat_id):
                store.release_cleanup(operation_id)
            return
    try:
        account_key = (getattr(record, "account_key", None) if record is not None else None) or current_account_key()
        async with _media_account_lock(store, account_key=account_key, media_type=media_type):
            if not store.is_worker_owner(operation_id, worker_token):
                return
            record = store.get(operation_id) if hasattr(store, "get") else record
            if record is not None and not operation_account_matches(record):
                return
            try:
                client = get_gemini_client()
                await await_before_deadline(initialize_client(), timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
            except ClientAuthenticationRequired:
                if (store.is_worker_owner(operation_id, worker_token)
                        and (record is None or operation_account_matches(record))):
                    if store.finish_worker(operation_id, worker_token=worker_token, state="failed",
                                           phase="authentication_required", verification_status="auth_required_before_dispatch",
                                           error_code="AUTH_REQUIRED"):
                        store.release_cleanup(operation_id)
                return
            except asyncio.TimeoutError:
                if (store.is_worker_owner(operation_id, worker_token)
                        and (record is None or operation_account_matches(record))):
                    _unknown(store, operation_id, worker_token, phase="media_initialize_timeout", code="TIMED_OUT")
                return
            if not store.is_worker_owner(operation_id, worker_token) or (record is not None and not operation_account_matches(record)):
                return
            timeout = media_timeout(media_type, request.get("timeout_seconds"))
            source_operation = getattr(record, "source_operation_id", None)
            source_artifact = getattr(record, "source_artifact_id", None)
            valid, files, _ = validate_image_paths(request.get("image_paths"))
            if not valid:
                raise MediaSourceUnavailable("A local image reference is no longer available.")
            if request.get("image_path"):
                files.append(request["image_path"])
            with ExitStack() as stack:
                if source_operation and source_artifact:
                    files.insert(0, stack.enter_context(source_image_upload(store, source_operation, source_artifact)))
                transport = request.get("web_media_transport")
                prompt = request["prompt"]
                if media_type == "image" and (image_aspect := request.get("aspect_ratio")):
                    if image_aspect not in prompt:
                        prompt = f"{prompt.rstrip()} (aspect ratio {image_aspect})"
                if not native_media_enabled(client, media_type, transport):
                    if request.get("edit") or source_operation:
                        prefix = ("Edit the first attached image using the other images as references: "
                                  if len(files) > 1 else "Edit this image: ")
                        prompt = prefix + prompt
                    else:
                        prompt = compose_media_prompt(media_type, prompt)
                if not store.is_worker_owner(operation_id, worker_token) or (record is not None and not operation_account_matches(record)):
                    return
                stack.enter_context(media_timeout_scope(client, timeout))
                generation_arguments = dict(prompt=prompt, files=files or None, model=settings["request_model"],
                                            thinking_level=request.get("thinking_level", "standard"), timeout=timeout)
                if transport is not None:
                    generation_arguments["web_media_transport"] = transport
                response = await asyncio.wait_for(generate_media_response(
                    client, arguments=generation_arguments,
                    schedule_cleanup=schedule_remote_chat_cleanup_from_response,
                    retain_chat=request.get("retain_chat", False),
                    delete_after_seconds=request.get("delete_after_seconds"), source="media_job",
                    media_type=media_type, duration_seconds=request.get("duration_seconds"),
                    aspect_ratio=request.get("aspect_ratio"),
                    **music_option_arguments(
                        request.get("music_duration"),
                        request.get("music_vocals"),
                        request.get("music_genre"),
                    ),
                    checkpoint=lambda response: _checkpoint(store, operation_id, worker_token, response),
                ), timeout=timeout)
            if not store.is_worker_owner(operation_id, worker_token):
                return
            record = store.get(operation_id)
            if record is not None:
                await _save_response(client, response, record, store=store, token=worker_token)
    except _LostMediaLease:
        return
    except NativeMediaGenerationError as error:
        saved_record = store.get(operation_id)
        if saved_record is None or not operation_account_matches(saved_record):
            return
        if not error.submitted and not saved_record.upstream_chat_id:
            if store.finish_worker(operation_id, worker_token=worker_token, state="failed",
                                   phase=(f"native_media_{error.stage}_not_submitted" if error.stage else
                                          "native_media_not_submitted"), verification_status="not_dispatched",
                                   error_code=error.code):
                store.release_cleanup(operation_id)
        else:
            phase = ("native_media_submitted" if saved_record.upstream_chat_id and
                     not saved_record.upstream_response_id else "native_media_outcome_unverified")
            _unknown(store, operation_id, worker_token, phase=phase, code=error.code)
    except MediaSourceUnavailable:
        if (record is not None and operation_account_matches(record) and not record.upstream_chat_id
                and store.finish_worker(operation_id, worker_token=worker_token, state="failed",
                                        phase="image_input_unavailable", verification_status="input_rejected",
                                        error_code="CAPABILITY_UNAVAILABLE")):
            store.release_cleanup(operation_id)
    except (VideoTransportChanged, VideoModeUnavailable) as error:
        code = "UPSTREAM_CHANGED" if isinstance(error, VideoTransportChanged) else "CAPABILITY_UNAVAILABLE"
        _unknown(store, operation_id, worker_token, phase="video_transport_unverified", code=code)
    except WebWorkflowTransportChanged:
        _unknown(store, operation_id, worker_token, phase="media_transport_unverified", code="UPSTREAM_CHANGED")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failure = result_from_exception(exc, logger=logger, operation="media_job")
        _unknown(store, operation_id, worker_token,
                 code=failure.error.code.value if failure.error is not None else "INTERNAL_ERROR")


async def recover_media_job(record: Any, *, store: Any, worker_token: str) -> None:
    """Read one exact turn and candidate; never send a generation request again."""
    from ..client_wrapper import get_gemini_client, initialize_client

    if not operation_account_matches(record):
        return
    if reconcile_owned_media_cleanup(store, record):
        return
    if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
        return
    cid, rid, rcid = record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id
    rejection_checkpointed = (getattr(record, "phase", None) == "media_rejected"
                              and getattr(record, "error_code", None) in _REJECTIONS)
    native_identity_pending = (getattr(record, "phase", None) == "native_media_submitted"
                               and record.media_type in {"video", "music"} and not rid and not rcid)
    if not cid:
        _unknown(store, record.operation_id, worker_token, phase="media_identity_incomplete")
        return
    if not rejection_checkpointed and not native_identity_pending and (not rid or not rcid):
        try:
            client = get_gemini_client()
            await await_before_deadline(initialize_client(), timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
            if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
                return
            read_history = getattr(client, "read_chat_for_media", client.read_chat)
            history = await await_before_deadline(
                read_history(cid, limit=10), timeout=MEDIA_READBACK_TIMEOUT_SECONDS,
            )
        except Exception as error:
            if _rate_limited_read(error, record, store=store, token=worker_token):
                return
            if operation_account_matches(record):
                _unknown(store, record.operation_id, worker_token, phase="media_identity_incomplete")
            return
        if not operation_account_matches(record):
            return
        recovered = _recover_exact_chat_response(history, cid, rid) if history is not None else None
        if recovered is None:
            _unknown(store, record.operation_id, worker_token, phase="media_identity_incomplete")
            return
        _checkpoint(store, record.operation_id, worker_token, recovered)
        current = store.get(record.operation_id)
        if current is not None and store.is_worker_owner(record.operation_id, worker_token):
            await _save_response(client, recovered, current, store=store, token=worker_token,
                                 readback_timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
        return
    try:
        client = get_gemini_client()
        try:
            await await_before_deadline(initialize_client(), timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if operation_account_matches(record):
                _unknown(store, record.operation_id, worker_token, phase="media_initialize_timeout", code="TIMED_OUT")
            return
        if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
            return
        if rejection_checkpointed:
            await _finish_media_rejection(client, SimpleNamespace(cid=cid), record, store=store, token=worker_token,
                                          code=record.error_code,
                                          verification_status=getattr(record, "verification_status", None))
            return
        if native_identity_pending:
            try:
                response = await read_native_media_response(
                    client, cid, record.media_type, timeout=MEDIA_READBACK_TIMEOUT_SECONDS,
                )
            except Exception as error:
                if _rate_limited_read(error, record, store=store, token=worker_token):
                    return
                response = None
            if not operation_account_matches(record):
                return
            if response is None:
                _unknown(store, record.operation_id, worker_token, phase="native_media_submitted",
                         code="READBACK_UNVERIFIED")
                return
            _checkpoint(store, record.operation_id, worker_token, response)
            current = store.get(record.operation_id)
            if current is not None and store.is_worker_owner(record.operation_id, worker_token):
                await _save_response(client, response, current, store=store, token=worker_token,
                                     readback_timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
            return
        read_history = getattr(client, "read_chat_for_media", client.read_chat)
        matched: Any | None = None
        for attempt in range(MEDIA_READBACK_MAX_ATTEMPTS):
            if not store.is_worker_owner(record.operation_id, worker_token) or not operation_account_matches(record):
                return
            if reconcile_owned_media_cleanup(store, record):
                return
            try:
                history = await await_before_deadline(
                    read_history(cid, limit=10), timeout=MEDIA_READBACK_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                if operation_account_matches(record):
                    _unknown(store, record.operation_id, worker_token, phase="media_readback_timeout", code="TIMED_OUT")
                return
            except (OSError, ConnectionError):
                # Transient network/connection error: bounded retry only.
                if attempt + 1 < MEDIA_READBACK_MAX_ATTEMPTS:
                    await asyncio.sleep(MEDIA_READBACK_RETRY_DELAYS[attempt])
                    continue
                if operation_account_matches(record):
                    _unknown(store, record.operation_id, worker_token, phase="media_readback_unverified")
                return
            if not operation_account_matches(record):
                return
            if history is None:
                # Upstream index is still lagging; report readback unverified
                # without fabricating a match. Maintenance re-runs this job on
                # subsequent ticks when history is None.
                _unknown(store, record.operation_id, worker_token, phase="media_readback_unverified")
                return
            matched = _matching_response(history, cid, rid, rcid)
            if matched is not None:
                break
            # Wrong turn / no exact (cid, rid, rcid) match: identity is
            # unverified; never retry on a mismatched history.
            _unknown(store, record.operation_id, worker_token, phase="media_identity_unverified")
            return
        if matched is None:
            _unknown(store, record.operation_id, worker_token, phase="media_readback_unverified")
            return
        if store.is_worker_owner(record.operation_id, worker_token):
            await _save_response(client, matched, record, store=store, token=worker_token,
                                 readback_timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if _rate_limited_read(error, record, store=store, token=worker_token):
            return
        if operation_account_matches(record):
            _unknown(store, record.operation_id, worker_token, phase="media_readback_error", code="READBACK_UNVERIFIED")
