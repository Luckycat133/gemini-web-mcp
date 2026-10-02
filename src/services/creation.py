"""Artifact-first creation and read-only recovery shared by MCP surfaces."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..client_wrapper import (
    cleanup_due_remote_chats, finalize_generated_chat_cleanup,
    get_gemini_client, initialize_client,
)
from ..constants import resolve_media_request
from ..domain import Artifact, ArtifactKind, ArtifactResultData, ArtifactState, DomainErrorCode, DomainResult, OperationState
from ..domain.conversations import is_valid_remote_chat_id
from ..thinking_client import MediaRequestObservation, client_request_timeout
from .artifacts import (
    artifact_from_local_path, classify_media_artifact_state, extract_response_artifacts,
    media_artifacts, media_operation_timeout, merge_artifacts,
    observed_backend_from_response, response_chat_id,
)
from .inputs import validate_optional_image_path
from .media_generation import (
    MediaGenerationAttempt, materialize_generated_media, media_artifact_result,
    media_creation_response, media_exception_result, media_generation_kwargs,
    media_requires_recovery, with_media_cleanup,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreationRequest:
    prompt: str
    media_type: str
    model: str = "flash"
    thinking_level: str = "standard"
    image_path: str | None = None
    timeout_seconds: int | None = None
    retain_chat: bool = False
    delete_after_seconds: int | None = None
    output_dir: str | None = None
    filename: str | None = None


def valid_media_filename(filename: str | None) -> bool:
    return filename is None or bool(
        filename.strip() and filename not in {".", ".."}
        and Path(filename).name == filename
        and not any(c in filename for c in ("/", "\\", "\x00"))
        and not any(ord(c) < 32 for c in filename)
    )


class CreationService:
    """A single attempt; failures preserve observed locators and never restart it."""

    def __init__(
        self, *, client_provider: Callable[[], Any] | None = None,
        initializer: Any = None, cleanup_due: Any = None,
        finalizer: Any = None, music_fetcher: Any = None,
        timeout_provider: Any = None, source: str = "creation",
        recovery_artifact_recorder: Any = None,
    ) -> None:
        self._client_provider = client_provider or get_gemini_client
        self._initializer = initializer or initialize_client
        self._cleanup_due = cleanup_due or cleanup_due_remote_chats
        self._finalizer = finalizer or finalize_generated_chat_cleanup
        self._music_fetcher = music_fetcher
        self._timeout_provider = timeout_provider or media_operation_timeout
        self._source = source
        self._recovery_artifact_recorder = recovery_artifact_recorder

    def validate(self, request: CreationRequest) -> DomainResult[None] | None:
        """Reject malformed starts before authentication or durable allocation."""
        if not request.prompt.strip():
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "prompt must not be blank.")
        if request.media_type not in {"image", "image_edit", "video", "music"}:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Unsupported media_type.")
        if not valid_media_filename(request.filename):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "filename must be a single file name.")
        valid, image_path, message = validate_optional_image_path(request.image_path)
        if not valid or (request.media_type == "image_edit" and not image_path):
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, message or "image_path is required for editing.")
        try:
            resolve_media_request(request.model, "image" if request.media_type == "image_edit" else request.media_type, request.thinking_level)
        except ValueError:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Invalid model or thinking level.")
        return None

    async def generate(
        self, request: CreationRequest,
        on_chat_observed: Callable[[str], None] | None = None,
        *, response_sink: list[Any] | None = None,
        on_artifacts_observed: Callable[..., None] | None = None,
    ) -> DomainResult[ArtifactResultData]:
        invalid = self.validate(request)
        if invalid is not None:
            return DomainResult(ok=False, data=None, error=invalid.error, warnings=invalid.warnings, meta=invalid.meta)
        _, image_path, _ = validate_optional_image_path(request.image_path)
        backend = resolve_media_request(request.model, "image" if request.media_type == "image_edit" else request.media_type, request.thinking_level)
        timeout = self._timeout_provider(request.media_type, request.timeout_seconds)
        inputs: tuple[Artifact, ...] = () if image_path is None else (await asyncio.to_thread(
            artifact_from_local_path, ArtifactKind.IMAGE, image_path,
            requested_backend=request.model, request_model=backend["request_model"],
            effective_backend=backend["backend_label"],
        ),)
        attempt = MediaGenerationAttempt(MediaRequestObservation(on_chat_observed=on_chat_observed))
        response = None
        client = None
        observed: list[Artifact] = []
        failure: Exception | None = None
        save_failures: tuple[str, ...] = ()
        generation_attempted = False
        try:
            async with asyncio.timeout(timeout):
                client = self._client_provider()
                await self._initializer()
                await self._cleanup_due(client)
                with client_request_timeout(client, timeout):
                    generation_attempted = True
                    response = await attempt.generate(client, **media_generation_kwargs(
                        request.prompt, request.media_type, model=backend["request_model"],
                        thinking_level=request.thinking_level,
                        files=[image_path] if image_path else None, timeout_seconds=timeout,
                    ))
                    cid = response_chat_id(response)
                    if cid and on_chat_observed and attempt.observation.chat_id != cid:
                        on_chat_observed(cid)
                    materialized = await materialize_generated_media(
                        client, response, media_type=request.media_type, prompt=request.prompt,
                        requested_model=request.model, request_model=backend["request_model"],
                        effective_backend=backend["backend_label"], output_dir=request.output_dir,
                        filename=request.filename, artifact_sink=observed,
                        music_fetcher=self._music_fetcher,
                    )
                    observed = list(materialized.artifacts)
                    save_failures = tuple(materialized.saved.failures)
        except asyncio.CancelledError:
            # An observed source is retained by its operation. Provider work may
            # continue after local cancellation; do not delete or restart it.
            raise
        except Exception as error:
            failure = error
            if response is None:
                response = attempt.recovery_response
        artifacts = media_artifacts(merge_artifacts(observed), request.media_type)
        if response_sink is not None and response is not None:
            response_sink.append(media_creation_response(response))
        state = classify_media_artifact_state(response, artifacts, request.media_type)
        if state == ArtifactState.EMPTY and save_failures:
            state = ArtifactState.FAILED
        if failure is not None and not artifacts:
            state = ArtifactState.FAILED
        if response is not None:
            inputs = tuple(replace(
                artifact, source_chat_id=response_chat_id(response),
                observed_backend=observed_backend_from_response(response),
            ) for artifact in inputs)
        data = ArtifactResultData(
            state=state, artifacts=artifacts, input_artifacts=inputs,
            requested_model=request.model, request_model=backend["request_model"],
            effective_backend=backend["backend_label"], observed_backend=observed_backend_from_response(response),
            source_chat_id=response_chat_id(response), media_type=request.media_type,
        )
        if failure is not None:
            result = media_exception_result(failure, data, logger=logger, operation="creation")
            if not generation_attempted:
                result = replace(result, meta=replace(
                    result.meta, verification_status="generation_not_started",
                    details={**result.meta.details, "generation_attempted": False},
                ))
        else:
            result = media_artifact_result(
                data, response=response, save_failures=save_failures,
                empty_retryable=False,
                empty_suggested_action=(
                    "Inspect the source chat and https://gemini.google.com/videos before another video request."
                    if request.media_type == "video"
                    else "Inspect the source chat and capability state before another creation request."
                ),
            )
        if response is not None:
            if on_artifacts_observed is not None:
                try:
                    on_artifacts_observed(
                        data.artifacts, operation_state=result.meta.operation_state,
                        verification_status=result.meta.verification_status,
                    )
                except Exception as error:
                    # A usable file must reach durable locators before its only
                    # remote recovery source can be removed.
                    failure = error
                    result = media_exception_result(error, data, logger=logger, operation="creation.persist")
            cleanup = await self._finalizer(
                response, owns_chat=True, retain_chat=request.retain_chat,
                preserve_for_recovery=media_requires_recovery(
                    data, response=response, save_failed=bool(save_failures), operation_failed=failure is not None,
                ), delete_after_seconds=request.delete_after_seconds,
                source=f"{self._source}:{request.media_type}", client=client,
            )
            result = with_media_cleanup(result, cleanup)
        return result

    async def recover(self, record: Any) -> DomainResult[ArtifactResultData]:
        """Inspect only this operation's observed source; never submit a prompt."""
        media_type = record.operation_type
        if media_type not in {"image", "image_edit", "video", "music"}:
            return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Not a media operation.")
        cid = record.upstream_chat_id
        # Durable locators carry no titles or raw result text. Recheck local
        # bytes independently after a restart, including after source deletion.
        stored: list[Artifact] = []
        for locator in record.artifacts:
            if locator.local_path:
                artifact = await asyncio.to_thread(
                    artifact_from_local_path, ArtifactKind(locator.kind), locator.local_path,
                    uri=locator.uri, source_chat_id=cid,
                )
                stored.append(replace(artifact, id=locator.id))
            else:
                stored.append(locator if isinstance(locator, Artifact) else locator.artifact())
        if stored:
            data = ArtifactResultData(
                state=classify_media_artifact_state(None, stored, media_type),
                artifacts=tuple(stored), source_chat_id=cid, media_type=media_type,
            )
            # Preserve remote-only outputs too; a partial music/video download
            # must not become complete merely because one local file exists.
            if record.state == OperationState.COMPLETED and all(locator.local_path for locator in record.artifacts):
                return media_artifact_result(data, response=None)
        if not is_valid_remote_chat_id(cid):
            return DomainResult.failure(
                DomainErrorCode.UPSTREAM_CHANGED,
                "The interrupted request has no observed source chat ID; do not start it again automatically.",
                retryable=False, verification_status="source_unobserved",
            )
        client = self._client_provider()
        observed: list[Artifact] = list(stored)
        response = None
        try:
            async with asyncio.timeout(120):
                await self._initializer()
                history = await client.read_chat(cid, limit=5)
                if history is not None and getattr(history, "cid", cid) != cid:
                    raise ValueError("Media history identity mismatch.")
                outputs = [
                    turn.model_output for turn in getattr(history, "turns", ())
                    if getattr(turn, "role", None) == "model" and getattr(turn, "model_output", None) is not None
                ]
                if outputs:
                    response = media_creation_response(outputs[0])
                    if response_chat_id(response) != cid:
                        raise ValueError("Media output identity mismatch.")
                else:
                    # SDK None may mean processing, a failed read or changed
                    # shape. None is never evidence of empty output or absence.
                    response = SimpleNamespace(metadata=[cid], images=[], videos=[], media=[], queued=True)
                observed.extend(media_artifacts(extract_response_artifacts(response), media_type))
                output_dir = getattr(record, "output_dir", None) or "generated_media"
                materialized = await materialize_generated_media(
                    client, response, media_type=media_type, prompt=record.operation_id,
                    requested_model="recovered", request_model=None, effective_backend=None,
                    output_dir=output_dir, artifact_sink=observed,
                    existing_artifacts=stored,
                )
                artifacts = media_artifacts(merge_artifacts(stored, materialized.artifacts), media_type)
                data = ArtifactResultData(
                    state=classify_media_artifact_state(response, artifacts, media_type),
                    artifacts=artifacts, source_chat_id=cid, media_type=media_type,
                )
                result = media_artifact_result(data, response=response, save_failures=materialized.saved.failures)
                if self._recovery_artifact_recorder is not None:
                    recorder = self._recovery_artifact_recorder
                else:
                    from .operations import persist_recovery_artifacts
                    recorder = persist_recovery_artifacts
                recorder(record, data.artifacts, operation_state=result.meta.operation_state,
                         verification_status=result.meta.verification_status)
        except Exception as error:
            data = ArtifactResultData(
                state=ArtifactState.FAILED, artifacts=tuple(observed), source_chat_id=cid, media_type=media_type,
            )
            return media_exception_result(error, data, logger=logger, operation="creation.recover")
        # Recovery carries a stored source from an owned operation. Only ready
        # verified local output may enter automatic cleanup.
        cleanup = await self._finalizer(
            response, owns_chat=True, preserve_for_recovery=media_requires_recovery(
                data, response=response, save_failed=bool(materialized.saved.failures),
            ), retain_chat=getattr(record, "retain_chat", True),
            delete_after_seconds=getattr(record, "delete_after_seconds", None),
            source=f"creation.recover:{media_type}", client=client,
        )
        return with_media_cleanup(result, cleanup)


def get_creation_service() -> CreationService:
    return CreationService()
