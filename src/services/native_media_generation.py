"""Single-submit native media generation with account and turn identity checks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
import inspect
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
from typing import Any

from ..constants import (
    gemini_4_model_family, is_generation_failure_reply, is_music_notation_reply,
    is_music_text_blueprint_reply, is_music_tool_unavailable_reply, resolve_thinking_level_id,
)
from ..domain.results import classify_exception
from ..infrastructure.account_identity import current_account_key
from ..infrastructure.native_media_browser import run_native_media_browser
from ..infrastructure.rpc_contracts import read_completed_empty_media_turn, read_completed_music_failure_turn
from ..tools.utils import validate_optional_image_path
from .client_timeouts import client_readback_scope
from .media_generation import MusicDuration, MusicGenre, MusicVocals, music_option_arguments, music_options_error
from .model_policy import ModelPolicyError, ModelSelection, account_model_tier, membership_restriction, model_policy_family

_CHAT_ID = re.compile(r"c_[a-f0-9]{16}")
_MAX_MEDIA_BYTES = 100 * 1024 * 1024
_HISTORY_POLL_SECONDS = 15.0
_MIN_PRE_SUBMIT_RETRY_BROWSER_SECONDS = 60.0


class NativeMediaGenerationError(RuntimeError):
    def __init__(self, code: str, *, submitted: bool = False, chat_id: str | None = None,
                 stage: str | None = None, message: str | None = None):
        super().__init__(message or "Native media generation could not be verified.")
        self.code, self.submitted, self.chat_id = code, submitted, chat_id
        self.stage = stage if stage in {
            "request", "open_composer", "open_tools", "media_tool", "media_intro", "video_reference", "video_options", "music_options", "video_duration", "video_aspect",
            "music_duration", "music_vocals", "music_genre", "video_format", "edit_prompt", "model_selection", "thinking_level", "verify_composer", "send", "observe",
        } else None


def native_media_enabled(client: Any, media_type: str, transport: str | None = None) -> bool:
    """Keep legacy clients compatible and require an explicit SDK transport override."""
    if media_type not in {"music", "video"}:
        return False
    selected = transport or os.environ.get("GEMINI_WEB_MEDIA_TRANSPORT", "native")
    if selected not in {"native", "sdk"}:
        raise NativeMediaGenerationError("INVALID_ARGUMENT")
    return selected == "native" and callable(getattr(type(client), "_resolve_policy_model", None))


class _NativeMedia:
    """Expose observed bytes to the existing independent media-storage verifier."""

    def __init__(self, asset: dict[str, Any], kind: str, client: Any, account_matches: Callable[[], bool]):
        self._asset, self._client, self._account_matches = asset, client, account_matches
        self.url = asset["url"] if kind == "video" else ""
        self.mp3_url = asset["url"] if kind == "music" else ""
        self.title = None

    async def save(self, *, path: str, filename: str, download_type: str | None = None, **_: Any) -> dict[str, str]:
        if not self._account_matches():
            raise PermissionError("Gemini account changed.")
        body = self._asset.get("content")
        if body is None:
            session = getattr(self._client, "client", None)
            if session is None:
                raise ValueError("The verified media download session is unavailable.")
            response = await session.get(self._asset["url"], allow_redirects=False)
            if response.status_code != 200:
                raise ValueError("The media download did not return complete bytes.")
            body = response.content
        if not isinstance(body, bytes) or not 0 < len(body) <= _MAX_MEDIA_BYTES or not self._account_matches():
            raise ValueError("The observed media bytes could not be verified.")
        expected = self._asset.get("byte_size")
        if expected is not None and (type(expected) is not int or expected != len(body)):
            raise ValueError("The observed media size changed.")
        destination = Path(path) / filename
        destination.write_bytes(body)
        return {download_type or "video": str(destination.absolute())}


def _exact_new_chat_response(history: Any, chat_id: str) -> Any | None:
    """A fresh native chat must have one observed model turn and one candidate."""
    outputs = []
    for turn in getattr(history, "turns", ()) or ():
        response = getattr(turn, "model_output", None)
        if getattr(turn, "role", None) != "model" or response is None:
            continue
        metadata = getattr(response, "metadata", ())
        candidates = getattr(response, "candidates", ())
        if (not isinstance(metadata, (list, tuple)) or len(metadata) < 2 or metadata[0] != chat_id
                or not isinstance(metadata[1], str) or not metadata[1].startswith("r_")
                or not isinstance(candidates, (list, tuple)) or len(candidates) != 1
                or not isinstance(getattr(candidates[0], "rcid", None), str)
                or not candidates[0].rcid.startswith("rc_")
                or (len(metadata) > 2 and metadata[2] and metadata[2] != candidates[0].rcid)):
            return None
        outputs.append(response)
    return outputs[0] if len(outputs) == 1 else None


async def read_native_media_response(
    client: Any, chat_id: str, kind: str, selection: ModelSelection | None = None,
    *, observe: Callable[[Any], None] | None = None, timeout: float = 30,
) -> Any | None:
    """Read a unique native-owned turn without selecting another turn or starting work."""
    if not _CHAT_ID.fullmatch(chat_id) or kind not in {"music", "video"}:
        return None
    media_read = getattr(client, "read_chat_for_media", None)
    read = media_read if callable(media_read) else client.read_chat
    # Keep the timeout in the caller task: a child wait_for can swallow cancel
    # if the read completes at the same time on Python 3.11.
    async with asyncio.timeout(timeout):
        history = await read(chat_id)
    output = _exact_new_chat_response(history, chat_id)
    if output is None:
        return None
    response = SimpleNamespace(metadata=list(output.metadata), rcid=output.candidates[0].rcid,
        text=getattr(output, "text", ""), images=[], videos=list(getattr(output, "videos", ()) or ()),
        media=list(getattr(output, "media", ()) or ()), candidates=None,
        _mcp_model_selection=selection, _mcp_native_media=True)
    if observe is not None:
        observe(response)
    return response


async def _verified_native_failure(client: Any, response: Any, chat_id: str, kind: str, *, timeout: float) -> bool:
    """A known notice only ends local observation after independent exact-turn proof."""
    text = getattr(response, "text", None)
    metadata = getattr(response, "metadata", ())
    rcid = getattr(response, "rcid", None)
    if (not isinstance(text, str)
            or not (is_generation_failure_reply(text) or kind == "music" and (
                is_music_tool_unavailable_reply(text) or is_music_text_blueprint_reply(text)
                or is_music_notation_reply(text)))
            or any(getattr(response, name, ()) for name in ("images", "videos", "media"))
            or not isinstance(metadata, (list, tuple)) or len(metadata) < 2 or metadata[0] != chat_id
            or not isinstance(metadata[1], str) or not metadata[1].startswith("r_")
            or not isinstance(rcid, str) or not rcid.startswith("rc_")):
        return False
    try:
        with client_readback_scope(client):
            async with asyncio.timeout(timeout):
                proof = await read_completed_empty_media_turn(client, chat_id, metadata[1], rcid, text)
            if proof.status == "rejected" and proof.reject_code == 429:
                raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=chat_id)
            if proof.status != "success" and kind == "music" and is_music_tool_unavailable_reply(text):
                async with asyncio.timeout(timeout):
                    proof = await read_completed_music_failure_turn(client, chat_id, metadata[1], rcid, text)
                if proof.status == "rejected" and proof.reject_code == 429:
                    raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=chat_id)
    except Exception as error:
        if classify_exception(error).error_code == "RATE_LIMITED":
            raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=chat_id) from None
        return False
    return proof.status == "success"


async def generate_native_media_response(
    client: Any, *, arguments: dict[str, Any], media_type: str,
    observe: Callable[[Any], None], duration_seconds: int | None = None, aspect_ratio: str | None = None,
    music_duration: MusicDuration | None = None, music_vocals: MusicVocals | None = None,
    music_genre: MusicGenre | None = None,
) -> Any:
    if music_options_error(
        media_type=media_type, music_duration=music_duration, music_vocals=music_vocals, music_genre=music_genre,
    ):
        raise NativeMediaGenerationError("INVALID_ARGUMENT")
    timeout = float(arguments.get("timeout") or 600)
    deadline = time.monotonic() + timeout
    readback_budget = min(30.0, timeout / 5)
    if media_type not in {"music", "video"} or arguments.get("chat"):
        raise NativeMediaGenerationError("CAPABILITY_UNAVAILABLE")
    reference_image = None
    if references := arguments.get("files"):
        if (media_type != "video" or not isinstance(references, list) or len(references) != 1
                or not isinstance(references[0], str)):
            raise NativeMediaGenerationError("CAPABILITY_UNAVAILABLE")
        valid, reference_image, _ = validate_optional_image_path(references[0])
        if not valid or reference_image is None:
            raise NativeMediaGenerationError("INVALID_ARGUMENT")
    requested = arguments.get("model") or "flash"
    selected = client._resolve_policy_model(requested)
    tier = account_model_tier(client)
    if tier is None:
        raise ModelPolicyError("Gemini membership could not be verified before native media generation.")
    if restriction := membership_restriction(tier, model_policy_family(selected), "media"):
        raise ModelPolicyError(restriction)
    # The registry description is localized independently of the browser UI.
    # Current Gemini 4 menu rows also expose their stable technical family label.
    family = gemini_4_model_family(selected.model_name)
    label = f"4 {family.title()}" if family else selected.display_name
    if not isinstance(label, str) or not label.strip():
        raise ModelPolicyError("The discovered model has no verified native selector label.")
    owner = current_account_key()
    try:
        cookies = dict(client.cookies.items())
    except (AttributeError, TypeError, ValueError):
        raise NativeMediaGenerationError("AUTH_REQUIRED") from None
    psid = cookies.get("__Secure-1PSID")

    def account_matches() -> bool:
        return bool(owner and psid and current_account_key() == owner
                    and os.environ.get("GEMINI_PSID") == psid
                    and client.cookies.get("__Secure-1PSID") == psid)

    if not account_matches():
        raise NativeMediaGenerationError("AUTH_REQUIRED")
    thinking_level = arguments.get("thinking_level") or "standard"
    effective_level = ({1: "low", 4: "medium", 2: "high"}.get(resolve_thinking_level_id(thinking_level) or 0)
                       if thinking_level != "standard" else None)
    selection = ModelSelection(str(requested), selected.model_name, None, effective_level is not None, effective_level)

    observed_chats: set[str] = set()
    on_chat_seen = False
    readback_lock = asyncio.Lock()
    readback_rate_limited = False

    async def readback(chat_id: str, read_timeout: float) -> Any | None:
        nonlocal readback_rate_limited
        async with readback_lock:
            if readback_rate_limited or not account_matches():
                return None
            try:
                return await read_native_media_response(client, chat_id, media_type, selection, timeout=read_timeout)
            except Exception as error:
                readback_rate_limited = classify_exception(error).error_code == "RATE_LIMITED"
                return None

    async def on_chat(chat_id: str) -> None:
        nonlocal on_chat_seen
        on_chat_seen = True
        if not _CHAT_ID.fullmatch(chat_id):
            raise NativeMediaGenerationError("UPSTREAM_CHANGED", submitted=True)
        if chat_id in observed_chats:
            return
        observed_chats.add(chat_id)
        observe(SimpleNamespace(metadata=[chat_id], rcid="", _mcp_model_selection=selection, _mcp_native_media=True))
        response = await readback(chat_id, 5)
        if response is not None and account_matches():
            observe(response)

    options: dict[str, Any] = {}
    parameters = inspect.signature(run_native_media_browser).parameters
    if reference_image is not None:
        if "reference_image_path" not in parameters:
            raise NativeMediaGenerationError("CAPABILITY_UNAVAILABLE")
        options["reference_image_path"] = reference_image
    for music_name, music_value in music_option_arguments(music_duration, music_vocals, music_genre).items():
        if music_name not in parameters:
            raise NativeMediaGenerationError("CAPABILITY_UNAVAILABLE")
        options[music_name] = music_value
    if thinking_level != "standard":
        if "thinking_level" not in parameters or effective_level is None:
            raise NativeMediaGenerationError("CAPABILITY_UNAVAILABLE")
        options["thinking_level"] = thinking_level
    for name, value, default in (("duration_seconds", duration_seconds, 10), ("aspect_ratio", aspect_ratio, "16:9")):
        if value is not None and name in parameters:
            options[name] = value
        elif value is not None and value != default:
            raise NativeMediaGenerationError("CAPABILITY_UNAVAILABLE")
    kind: Any = media_type
    for browser_attempt in range(2):
        browser_timeout = (timeout - readback_budget - 2 if browser_attempt == 0
                           else deadline - time.monotonic() - readback_budget - 2)
        browser = asyncio.create_task(run_native_media_browser(kind, arguments["prompt"], label, cookies,
            timeout=max(0.1, browser_timeout), on_chat=on_chat, account_matches=account_matches, **options))
        try:
            while not browser.done() and not readback_rate_limited:
                # DOM observation stays local and wakes this wait immediately when ready.
                # History is only a slower recovery path, not a per-frame network poll.
                await asyncio.wait({browser}, timeout=_HISTORY_POLL_SECONDS)
                if browser.done() or not observed_chats or readback_rate_limited or not account_matches():
                    continue
                chat_id = next(iter(observed_chats))
                recovered = await readback(chat_id, min(5.0, max(0.1, deadline - time.monotonic())))
                if recovered is not None and account_matches():
                    observe(recovered)
                    items = getattr(recovered, "media" if media_type == "music" else "videos", ()) or ()
                    try:
                        terminal = bool(items) or await _verified_native_failure(client, recovered, chat_id, media_type,
                            timeout=min(5.0, max(0.1, deadline - time.monotonic())))
                    except NativeMediaGenerationError:
                        readback_rate_limited = True
                        continue
                    if terminal and account_matches():
                        recovered.native_media_submitted = True
                        recovered.native_model_selector_verified = True
                        return recovered
            result = await browser
        finally:
            if not browser.done():
                browser.cancel()
            with suppress(asyncio.CancelledError):
                await browser
        if not (browser_attempt == 0 and result.state == "unverified" and result.error_code == "TIMED_OUT"
                and result.submitted is False and result.chat_id is None and not result.media
                and result.stage not in {"send", "observe"} and not on_chat_seen and not observed_chats
                and not readback_rate_limited and account_matches()
                # Reserve a normal UI-action budget as well as the existing read-back margin.
                and deadline - time.monotonic() - readback_budget - 2 >= _MIN_PRE_SUBMIT_RETRY_BROWSER_SECONDS):
            break
    if result.chat_id:
        await on_chat(result.chat_id)
    if (not result.submitted or not result.selected_model_verified or not result.chat_id or not account_matches()):
        raise NativeMediaGenerationError(
            result.error_code or "READBACK_UNVERIFIED",
            submitted=result.submitted,
            chat_id=result.chat_id,
            stage=result.stage,
            message=getattr(result, "error_message", None),
        )
    response = await readback(result.chat_id, max(0.1, min(readback_budget, deadline - time.monotonic())))
    if readback_rate_limited and account_matches():
        raise NativeMediaGenerationError("RATE_LIMITED", submitted=True, chat_id=result.chat_id, stage=result.stage)
    if response is None or not account_matches():
        raise NativeMediaGenerationError("READBACK_UNVERIFIED", submitted=True, chat_id=result.chat_id)
    observe(response)
    if result.state != "ready" or not result.media:
        response.native_error_code = result.error_code
        if getattr(result, "error_message", None):
            response.native_error_message = result.error_message
        response.native_media_submitted = True
        response.native_model_selector_verified = True
        return response
    if getattr(response, "media" if media_type == "music" else "videos", None):
        response.native_media_submitted = True
        response.native_model_selector_verified = True
        return response
    assets = [_NativeMedia(asset, media_type, client, account_matches) for asset in result.media]
    return SimpleNamespace(metadata=list(response.metadata), rcid=response.rcid,
        text=getattr(response, "text", ""), images=[], videos=assets if media_type == "video" else [],
        media=assets if media_type == "music" else [], candidates=None, _mcp_model_selection=selection,
        native_media_submitted=True, native_model_selector_verified=True, _mcp_native_media=True)
