"""Consume a media generation stream while protecting observed conversations."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
import inspect
from typing import Any, Callable

from ..domain import CleanupObservation, CleanupState, DomainResult, DomainWarning
from .artifacts import response_chat_id
from .client_timeouts import client_timeout_scope
from .cleanup_scope import observe_cleanup_frames
from .model_policy import model_policy_scope

IN_PROGRESS_CLEANUP_SECONDS = 1800
_MEDIA_PROMPT_PREFIXES = {
    "image": "Generate an image. Prompt: ",
    "video": "Generate a video using Gemini's video generation capability. Prompt: ",
    "music": "Create music/audio using Gemini's music generation capability. Prompt: ",
}


def compose_media_prompt(media_type: str, prompt: str) -> str:
    prefix = _MEDIA_PROMPT_PREFIXES[media_type]
    stripped = prompt.strip()
    return stripped if stripped.startswith(prefix) else prefix + stripped


def media_timeout(media_type: str, timeout_seconds: int | None = None) -> int:
    if timeout_seconds and timeout_seconds > 0:
        return timeout_seconds
    return 180 if media_type == "image" else 600


media_timeout_scope = client_timeout_scope

class VideoModeUnavailable(RuntimeError):
    code = "MODEL_UNAVAILABLE"


@dataclass
class MediaGenerationProgress:
    """Public-safe cleanup evidence retained even when the upstream stream fails."""
    last_chat_id: str | None = None
    registration_failures: dict[str, CleanupObservation] = field(default_factory=dict)


def add_media_cleanup_evidence(
    result: DomainResult[Any], progress: MediaGenerationProgress, cleanup: CleanupObservation | None = None,
) -> DomainResult[Any]:
    from dataclasses import replace

    failures = dict(progress.registration_failures)
    if cleanup is not None and cleanup.upstream_chat_id and cleanup.state not in {
        CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE,
    }:
        failures.pop(cleanup.upstream_chat_id, None)
    if not failures:
        return result
    warning = DomainWarning(
        code="cleanup_registration_unverified",
        message="A media chat could not be registered for automatic cleanup.",
        suggested_action="Use the reported chat ID to inspect or clean it up.",
    )
    return replace(result, warnings=(*result.warnings, warning), meta=replace(
        result.meta, details={**result.meta.details, "cleanup_registration_failures": tuple(failures.values())},
    ))


def _identity(response: Any) -> tuple[str, str, str] | None:
    metadata = getattr(response, "metadata", None)
    if not isinstance(metadata, (list, tuple)) or len(metadata) < 2:
        return None
    cid = response_chat_id(response)
    rid = metadata[1]
    try:
        rcid = getattr(response, "rcid", None)
    except (IndexError, AttributeError):
        rcid = None
    if not rcid and len(metadata) > 2:
        rcid = metadata[2]
    if not cid or not isinstance(rid, str) or not rid or not isinstance(rcid, str) or not rcid:
        return None
    return cid, rid, rcid


def _distinct(items: list[Any], key: Callable[[Any], Any]) -> list[Any]:
    seen: set[Any] = set()
    result = []
    for item in items:
        identity = key(item)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _selected_generated_images(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None)
    chosen = getattr(response, "chosen", 0)
    if not isinstance(candidates, (list, tuple)) or not isinstance(chosen, int) or not 0 <= chosen < len(candidates):
        return []
    return list(getattr(candidates[chosen], "generated_images", None) or ())


def _selected_generated_videos(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", None)
    chosen = getattr(response, "chosen", 0)
    if not isinstance(candidates, (list, tuple)) or not isinstance(chosen, int) or not 0 <= chosen < len(candidates):
        return []
    return list(getattr(candidates[chosen], "generated_videos", None) or ())


class _CandidateView:
    def __init__(self, candidate: Any, generated_images: list[Any], generated_videos: list[Any]):
        self._candidate = candidate
        self.generated_images = generated_images
        self.generated_videos = generated_videos

    def __getattr__(self, name: str) -> Any:
        return getattr(self._candidate, name)


class _ResponseView:
    def __init__(self, final: Any, earlier: list[Any], fallback_metadata: Any):
        self._final = final
        self.metadata = getattr(final, "metadata", None) if response_chat_id(final) else fallback_metadata
        self.media = _distinct(
            [item for chunk in (*earlier, final) for item in (getattr(chunk, "media", None) or ())],
            lambda item: (getattr(item, "mp3_url", ""), getattr(item, "url", ""))
            if getattr(item, "mp3_url", "") or getattr(item, "url", "") else id(item),
        )
        self.images = list(getattr(final, "images", None) or ())
        self.videos = list(getattr(final, "videos", None) or ())
        self.candidates = getattr(final, "candidates", None)
        chosen = getattr(final, "chosen", 0)
        if isinstance(self.candidates, (list, tuple)) and isinstance(chosen, int) and 0 <= chosen < len(self.candidates):
            selected = self.candidates[chosen]
            if hasattr(selected, "generated_images") or hasattr(selected, "generated_videos"):
                generated = _distinct(
                    [item for chunk in (*earlier, final) for item in _selected_generated_images(chunk)],
                    lambda item: getattr(item, "url", None) or id(item),
                )
                self.images = _distinct([*self.images, *generated], lambda item: getattr(item, "url", None) or id(item))
                videos = _distinct(
                    [item for chunk in (*earlier, final) for item in _selected_generated_videos(chunk)],
                    lambda item: getattr(item, "url", None) or id(item),
                )
                self.videos = _distinct([*self.videos, *videos], lambda item: getattr(item, "url", None) or id(item))
                self.candidates = list(self.candidates)
                self.candidates[chosen] = _CandidateView(selected, generated, videos)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._final, name)


async def generate_media_response(
    client: Any,
    *,
    arguments: dict[str, Any],
    schedule_cleanup: Callable[..., Any],
    retain_chat: bool,
    delete_after_seconds: int | None,
    source: str,
    media_type: str = "",
    progress: MediaGenerationProgress | None = None,
    checkpoint: Callable[[Any], None] | None = None,
) -> Any:
    """Keep the CID durable as soon as a stream yields it; do not restart work."""
    observed: set[str] = set()
    seen_cids: set[str] = set()
    progress = progress or MediaGenerationProgress()
    responses: list[Any] = []
    last_identified: Any = None

    def observe(response: Any) -> None:
        nonlocal last_identified
        cid = response_chat_id(response)
        if cid and checkpoint is not None:
            checkpoint(response)
        if cid:
            last_identified = response
            progress.last_chat_id = cid
            seen_cids.add(cid)
        if cid and cid not in observed:
            try:
                registration = schedule_cleanup(
                    response, retain_chat=retain_chat,
                    delete_after_seconds=(delete_after_seconds if retain_chat else
                                          max(IN_PROGRESS_CLEANUP_SECONDS, delete_after_seconds or 0)),
                    source=f"{source}:in_progress",
                )
                observation = getattr(registration, "cleanup_observation", registration)
                if not isinstance(observation, CleanupObservation) or observation.state in {
                    CleanupState.FAILED, CleanupState.INVALID_ID, CleanupState.NOT_APPLICABLE,
                }:
                    progress.registration_failures[cid] = (
                        observation if isinstance(observation, CleanupObservation) else CleanupObservation(
                            state=CleanupState.FAILED, upstream_chat_id=cid, source=f"{source}:in_progress",
                        )
                    )
                    return
            except Exception:
                progress.registration_failures[cid] = CleanupObservation(
                    state=CleanupState.FAILED, upstream_chat_id=cid, source=f"{source}:in_progress",
                )
                return
            progress.registration_failures.pop(cid, None)
            observed.add(cid)

    scope_factory = getattr(client, "video_generation_scope", None)
    if media_type == "video":
        if not callable(scope_factory):
            raise VideoModeUnavailable("Dedicated video model unavailable for this client.")
        scope = scope_factory()
    else:
        scope = nullcontext()
    arguments = dict(arguments)
    chat = arguments.get("chat")
    factory = getattr(type(client), "create_cleanup_chat", None)
    if chat is None and callable(factory):
        chat = factory(client)
        arguments["chat"] = chat
    if callable(factory):
        # SDK 2.1.1's decorated _generate defaults to five retries, which can
        # duplicate a request after its CID has already been observed.
        arguments.setdefault("current_retry", 0)
    try:
        with scope, observe_cleanup_frames(observe), model_policy_scope("media"):
            stream_method = getattr(client, "generate_content_stream", None)
            if callable(stream_method):
                async for response in stream_method(**arguments):
                    observe(response)
                    responses.append(response)
            else:
                fallback_arguments = arguments
                try:
                    parameters = inspect.signature(client.generate_content).parameters
                    if "timeout" not in parameters and not any(
                        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
                    ):
                        fallback_arguments = {key: value for key, value in arguments.items() if key != "timeout"}
                except (TypeError, ValueError):
                    pass
                response = await client.generate_content(**fallback_arguments)
                observe(response)
                responses.append(response)
    except BaseException:
        # Preserve the original failure or cancellation even if the final
        # best-effort observation cannot be durably registered.
        if chat is not None:
            try:
                observe(chat)
            except BaseException:
                pass
        raise
    if not responses:
        raise RuntimeError("Media generation returned no response.")
    final = responses[-1]
    identity = _identity(final)
    matches = [response for response in responses[:-1] if identity is not None and _identity(response) == identity]
    metadata = getattr(last_identified, "metadata", None) if len(seen_cids) == 1 else None
    if not matches and (response_chat_id(final) or not metadata):
        return final
    return _ResponseView(final, matches, metadata)
