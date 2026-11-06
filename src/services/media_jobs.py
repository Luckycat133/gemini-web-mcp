"""Metadata-only background media execution and identity-bound recovery."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

from ..constants import is_generation_failure_reply, resolve_media_request
from ..client_manager import ClientAuthenticationRequired
from ..domain import ArtifactKind, ArtifactState
from ..infrastructure.account_identity import operation_account_matches
from ..infrastructure.rpc_contracts import VideoTransportChanged
from .artifacts import is_response_queued, observed_backend_from_response, response_chat_id
from .deadlines import await_before_deadline
from .media_generation import (
    VideoModeUnavailable, compose_media_prompt, generate_media_response, media_timeout, media_timeout_scope,
)
from .media_storage import ImageDecoderUnavailable, _save_generated_media, _valid_media_filename, require_image_decoder
from .music_recovery import MusicErrorCardObserved, fetch_music_media_from_chat, music_response_identity
from .web_context_transport import fetch_workflow_turn

MEDIA_READBACK_TIMEOUT_SECONDS = 60.0

_QUOTA_REJECTION = "I can create more images as soon as your limit resets. Check your usage in Settings."
_VIDEO_QUOTA_REJECTION = "I can create more videos as soon as your limit resets. Check your usage in Settings."
_VIDEO_REFUSAL = "请恕我爱莫能助，我只是一个语言模型，不能理解或回复你的这个问题。"
_MUSIC_ERROR_NOTICE = "生成这段钢琴和弦乐纯音乐时出现了一些问题。你要不要现在再重新尝试一次？"
_MUSIC_REFUSAL_ABC = ("I cannot render or stream playable audio files (such as MP3 or WAV) directly within this text "
                      "interface, but I can provide the complete original composition in standard ABC notation and a "
                      "playable script so you can hear ")
_MUSIC_QUOTA_PREFIX = "很抱歉，目前的音乐生成额度已达上限，暂时无法为你生成"
_MUSIC_REFUSAL = ("I'm unable to directly generate or output audio files here, as I don't have a built-in "
                  "direct audio synthesis tool attached to this chat interface.")
_REJECTIONS = {"UPSTREAM_LIMIT_EXCEEDED": "upstream_limit_message_observed",
               "ARTIFACT_NOT_RETURNED": "model_media_refusal_observed",
               "UPSTREAM_REJECTED": "upstream_media_error_card_observed"}


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
    if cid and not store.checkpoint_media(
        operation_id, worker_token=token, chat_id=cid, response_id=rid or None, candidate_id=rcid or None,
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
    store.finish_worker(operation_id, worker_token=token, state="queued" if queued else "partial", phase=phase,
                        verification_status="not_observed", error_code=code)


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


async def _finish_media_rejection(
    client: Any, response: Any, record: Any, *, store: Any, token: str, code: str,
) -> None:
    """Checkpoint the exact observed rejection before releasing cleanup protection."""
    if not operation_account_matches(record):
        return
    verification = ("upstream_media_failure_reply_observed"
                    if code == "UPSTREAM_REJECTED" and record.media_type == "image" else _REJECTIONS[code])
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
        _unknown(store, record.operation_id, token, phase="media_identity_incomplete")
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
    if (record.media_type == "image" and is_generation_failure_reply(image_failure)
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))):
        try:
            turn = await await_before_deadline(fetch_workflow_turn(client, cid, rid, rcid), timeout=readback_timeout)
        except Exception:
            turn = None
        if (turn and turn.get("completed") is True and turn.get("text") == image_failure
                and (turn.get("chat_id"), turn.get("rid"), turn.get("rcid")) == (cid, rid, rcid)):
            await _finish_media_rejection(client, response, record, store=store, token=token, code="UPSTREAM_REJECTED")
            return
    media_items = None
    if record.media_type == "music" and not getattr(response, "media", None):
        try:
            text = getattr(response, "text", None)
            requires_verified_empty = isinstance(text, str) and text.startswith(
                (_MUSIC_QUOTA_PREFIX, _MUSIC_REFUSAL, _MUSIC_REFUSAL_ABC)
            ) or text == _MUSIC_ERROR_NOTICE
            read_cards = (fetch_music_media_from_chat(client, cid, rid=rid, rcid=rcid, strict=True)
                          if requires_verified_empty else fetch_music_media_from_chat(client, cid, rid=rid, rcid=rcid))
            media_items = await await_before_deadline(read_cards, timeout=readback_timeout)
        except MusicErrorCardObserved:
            if (text == _MUSIC_ERROR_NOTICE
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
        except Exception:
            _unknown(store, record.operation_id, token, phase="media_readback_error", code="READBACK_UNVERIFIED")
            return
    if not operation_account_matches(record):
        return
    text = getattr(response, "text", None)
    if (record.media_type == "music" and isinstance(text, str) and text.startswith(_MUSIC_QUOTA_PREFIX)
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))
            and not media_items):
        await _finish_media_rejection(client, response, record, store=store, token=token, code="UPSTREAM_LIMIT_EXCEEDED")
        return
    if (record.media_type == "music" and isinstance(text, str) and text.startswith((_MUSIC_REFUSAL, _MUSIC_REFUSAL_ABC))
            and not any(getattr(response, name, None) for name in ("images", "videos", "media"))
            and not media_items):
        await _finish_media_rejection(client, response, record, store=store, token=token, code="ARTIFACT_NOT_RETURNED")
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
                 code="ARTIFACT_SAVE_FAILED" if saved.failures else None)
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
    if media_type not in {"image", "video", "music"} or not _valid_media_filename(filename):
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
        prompt = (f"Edit this image: {request['prompt']}" if request.get("edit") else
                  compose_media_prompt(media_type, request["prompt"]))
        files = [request["image_path"]] if request.get("image_path") else None
        with media_timeout_scope(client, timeout):
            response = await asyncio.wait_for(generate_media_response(
                client, arguments=dict(prompt=prompt, files=files, model=settings["request_model"],
                                       thinking_level=request.get("thinking_level", "standard"), timeout=timeout),
                schedule_cleanup=schedule_remote_chat_cleanup_from_response,
                retain_chat=request.get("retain_chat", False),
                delete_after_seconds=request.get("delete_after_seconds"), source="media_job",
                media_type=media_type, checkpoint=lambda response: _checkpoint(store, operation_id, worker_token, response),
            ), timeout=timeout)
        if not store.is_worker_owner(operation_id, worker_token):
            return
        record = store.get(operation_id)
        if record is not None:
            await _save_response(client, response, record, store=store, token=worker_token)
    except _LostMediaLease:
        return
    except (VideoTransportChanged, VideoModeUnavailable) as error:
        code = "UPSTREAM_CHANGED" if isinstance(error, VideoTransportChanged) else "CAPABILITY_UNAVAILABLE"
        _unknown(store, operation_id, worker_token, phase="video_transport_unverified", code=code)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _unknown(store, operation_id, worker_token, code="TIMED_OUT" if isinstance(exc, asyncio.TimeoutError) else None)


async def recover_media_job(record: Any, *, store: Any, worker_token: str) -> None:
    """Read one exact turn and candidate; never send a generation request again."""
    from ..client_wrapper import get_gemini_client, initialize_client

    if not operation_account_matches(record):
        return
    cid, rid, rcid = record.upstream_chat_id, record.upstream_response_id, record.upstream_candidate_id
    rejection_checkpointed = (getattr(record, "phase", None) == "media_rejected"
                              and getattr(record, "error_code", None) in _REJECTIONS)
    if not cid or (not rejection_checkpointed and (not rid or not rcid)):
        _unknown(store, record.operation_id, worker_token, phase="media_identity_incomplete")
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
                                          code=record.error_code)
            return
        try:
            history = await await_before_deadline(client.read_chat(cid, limit=10), timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            if operation_account_matches(record):
                _unknown(store, record.operation_id, worker_token, phase="media_readback_timeout", code="TIMED_OUT")
            return
        if not operation_account_matches(record):
            return
        if history is None:
            _unknown(store, record.operation_id, worker_token, phase="media_readback_unverified")
            return
        response = _matching_response(history, cid, rid, rcid)
        if response is None:
            _unknown(store, record.operation_id, worker_token, phase="media_identity_unverified")
            return
        if store.is_worker_owner(record.operation_id, worker_token):
            await _save_response(client, response, record, store=store, token=worker_token,
                                 readback_timeout=MEDIA_READBACK_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        if operation_account_matches(record):
            _unknown(store, record.operation_id, worker_token, phase="media_readback_error", code="READBACK_UNVERIFIED")
