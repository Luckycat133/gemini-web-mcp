"""Documented Gemini Interactions API for Omni video generation."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import cast

import httpx2
import orjson

from ..constants import OMNI_VIDEO_MODEL
from ..domain.results import DomainErrorCode

_API = "https://generativelanguage.googleapis.com/v1beta/"
_INTERACTION_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.=-]{0,511}")
_STATUSES = {
    "in_progress", "completed", "failed", "cancelled", "incomplete", "requires_action", "requires_continuation",
}
_MAX_VIDEO_BYTES = 64 * 1024 * 1024
# Allow the base64 result, echoed reference image and interaction metadata.
_MAX_JSON_BYTES = 108 * 1024 * 1024
_CHUNK_SIZE = 64 * 1024
MAX_OMNI_IMAGE_BYTES = 14_000_000  # Reserve space for base64 and the rest of the JSON input.


class OmniVideoError(RuntimeError):
    """An error safe to surface without request URLs, credentials or raw responses."""

    def __init__(self, code: DomainErrorCode, message: str, *, dispatch_rejected: bool = False):
        super().__init__(message)
        self.code = code
        self.dispatch_rejected = dispatch_rejected


@dataclass(frozen=True, slots=True)
class OmniVideoInteraction:
    status: str
    video: bytes | None
    observed_model: str | None


def _changed() -> OmniVideoError:
    return OmniVideoError(DomainErrorCode.UPSTREAM_CHANGED, "The Gemini video API returned an unexpected response.")


def _interaction_path(interaction_id: str) -> str:
    if not _INTERACTION_ID.fullmatch(interaction_id):
        raise OmniVideoError(DomainErrorCode.INVALID_ARGUMENT, "Invalid Gemini video interaction ID.")
    return f"interactions/{interaction_id}"


def _output_video(payload: dict[str, object]) -> bytes | None:
    # Stored GETs return inline data and can include user_input steps.
    steps = payload.get("steps", [])
    if not isinstance(steps, list):
        raise _changed()
    encoded: str | None = None
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("type"), str):
            raise _changed()
        if step["type"] != "model_output":
            continue
        parts = step.get("content")
        if not isinstance(parts, list):
            raise _changed()
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("type"), str):
                raise _changed()
            if part["type"] != "video":
                continue
            data = part.get("data")
            if (encoded is not None or not isinstance(data, str) or not data
                    or part.get("mime_type", "video/mp4") != "video/mp4"):
                raise _changed()
            encoded = data
    if encoded is None:
        return None
    if len(encoded) > 4 * ((_MAX_VIDEO_BYTES + 2) // 3):
        raise _changed()
    try:
        video = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise _changed() from None
    if not video or len(video) > _MAX_VIDEO_BYTES:
        raise _changed()
    return video


def _content_length(response: httpx2.Response, limit: int) -> int | None:
    length = response.headers.get("content-length")
    if length is None:
        return None
    if len(length) > 20 or not length.isascii() or not length.isdecimal() or int(length) > limit:
        raise _changed()
    return int(length)


async def _read_json(response: httpx2.Response, limit: int) -> object:
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise _changed()
    length = _content_length(response, limit)
    raw = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
        if len(raw) + len(chunk) > limit:
            raise _changed()
        raw.extend(chunk)
    if length is not None and len(raw) != length:
        raise _changed()
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        raise _changed() from None


async def _json_object(response: httpx2.Response) -> dict[str, object]:
    result = await _read_json(response, _MAX_JSON_BYTES)
    if not isinstance(result, dict):
        raise _changed()
    return cast(dict[str, object], result)


async def _http_error(response: httpx2.Response, *, creating: bool) -> OmniVideoError:
    status = response.status_code
    if status in (408, 504):
        return OmniVideoError(DomainErrorCode.TIMED_OUT, "The Gemini video API request timed out.")
    if status >= 500 or status in (409, 499):
        return OmniVideoError(DomainErrorCode.NETWORK_ERROR, "The Gemini video API request could not be confirmed.")
    if not 400 <= status < 500:
        return _changed()
    code = None
    invalid_key = False
    if status in (400, 403, 404):
        try:
            payload = await _read_json(response, _CHUNK_SIZE)
            if isinstance(payload, list) and len(payload) == 1:
                payload = payload[0]
            details = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(details, dict):
                reported = details.get("code")
                code = reported if isinstance(reported, str) else details.get("status")
                errors = details.get("details")
                invalid_key = isinstance(errors, list) and any(
                    isinstance(item, dict) and item.get("reason") in ("API_KEY_INVALID", "API_KEY_EXPIRED")
                    for item in errors
                )
        except (OmniVideoError, httpx2.RequestError):
            pass
    if invalid_key or status == 401 or code in ("authentication", "UNAUTHENTICATED"):
        error = DomainErrorCode.AUTH_REQUIRED
        message = "Gemini video API authentication failed. Check the configured API key."
    elif status in (402, 403) or code == "failed_precondition" or (status == 404 and creating):
        error = DomainErrorCode.CAPABILITY_UNAVAILABLE
        message = "Gemini Omni video is unavailable to this API project. Check model access and billing."
    elif status == 404 and code in ("not_found", "NOT_FOUND"):
        error = DomainErrorCode.SESSION_NOT_FOUND
        message = "The requested Gemini video API resource was not found."
    elif status == 429:
        error = DomainErrorCode.RATE_LIMITED
        message = "The Gemini video API rate or quota limit was reached."
    else:
        error = DomainErrorCode.UPSTREAM_REJECTED
        message = "The Gemini video API rejected the request."
    return OmniVideoError(error, message, dispatch_rejected=creating)


class OmniVideoAPI:
    """Use the official API with a dedicated, cookie-free HTTP client."""

    def __init__(self, http: httpx2.AsyncClient, api_key: str):
        if not api_key or not api_key.isascii() or any(ord(char) <= 32 or ord(char) >= 127 for char in api_key):
            raise OmniVideoError(DomainErrorCode.AUTH_REQUIRED, "A valid Gemini video API key is required.",
                                 dispatch_rejected=True)
        self._http = http
        self._headers = {"x-goog-api-key": api_key, "Api-Revision": "2026-05-20", "Accept-Encoding": "identity"}

    async def _request(self, method: str, path: str, *, body: dict[str, object] | None = None,
                       read_body: bool = True) -> dict[str, object]:
        try:
            async with self._http.stream(method, _API + path, json=body, headers=self._headers,
                                         auth=None, follow_redirects=False) as response:
                if not response.is_success:
                    raise await _http_error(response, creating=method == "POST")
                return await _json_object(response) if read_body else {}
        except httpx2.TimeoutException:
            raise OmniVideoError(DomainErrorCode.TIMED_OUT, "The Gemini video API request timed out.") from None
        except httpx2.RequestError:
            raise OmniVideoError(DomainErrorCode.NETWORK_ERROR, "The Gemini video API request could not be confirmed.") from None

    async def create(self, prompt: str, *, image: tuple[str, bytes] | None = None,
                     duration_seconds: int | None = None) -> str:
        """Send the paid mutation once, keeping only the returned opaque identity."""
        if not prompt.strip():
            raise OmniVideoError(DomainErrorCode.INVALID_ARGUMENT, "A video prompt is required.", dispatch_rejected=True)
        # Request URI delivery for larger clips. Google documents that stored
        # interaction GETs still return inline MP4 data for this model.
        response_format = {"type": "video", "delivery": "uri"}
        if duration_seconds is not None:
            if type(duration_seconds) is not int or not 3 <= duration_seconds <= 15:
                raise OmniVideoError(DomainErrorCode.INVALID_ARGUMENT, "Video duration must be 3 to 15 whole seconds.",
                                     dispatch_rejected=True)
            response_format["duration"] = f"{duration_seconds}s"
        content: str | list[dict[str, str]] = prompt
        if image is not None:
            mime_type, data = image
            if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"} or not (
                0 < len(data) <= MAX_OMNI_IMAGE_BYTES
            ):
                raise OmniVideoError(DomainErrorCode.INVALID_ARGUMENT, "Invalid Omni video reference image.",
                                     dispatch_rejected=True)
            content = [{"type": "image", "mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")},
                       {"type": "text", "text": prompt}]
        body: dict[str, object] = {"model": OMNI_VIDEO_MODEL, "input": content, "response_format": response_format,
                                   "background": True, "store": True}
        response = await self._request("POST", "interactions", body=body)
        interaction_id = response.get("id")
        if not isinstance(interaction_id, str) or not _INTERACTION_ID.fullmatch(interaction_id):
            raise _changed()
        return interaction_id

    async def read(self, interaction_id: str) -> OmniVideoInteraction:
        response = await self._request("GET", _interaction_path(interaction_id))
        status, model = response.get("status"), response.get("model")
        if response.get("id") != interaction_id or not isinstance(status, str) or status not in _STATUSES:
            raise _changed()
        if model is not None and model != OMNI_VIDEO_MODEL:
            raise _changed()
        return OmniVideoInteraction(status, _output_video(response) if status == "completed" else None,
                                    OMNI_VIDEO_MODEL if model == OMNI_VIDEO_MODEL else None)

    async def delete_and_verify(self, interaction_id: str) -> bool:
        """Delete one interaction and confirm absence with a separate read."""
        path = _interaction_path(interaction_id)
        try:
            await self._request("DELETE", path, read_body=False)
        except OmniVideoError:
            pass  # The mutation may have succeeded before a connection failed.
        try:
            await self._request("GET", path, read_body=False)
        except OmniVideoError as error:
            return error.code == DomainErrorCode.SESSION_NOT_FOUND
        return False
