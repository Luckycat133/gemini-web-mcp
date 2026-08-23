"""Shared mixed-input understanding application service for assistance surfaces."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..domain import (
    Artifact,
    ArtifactKind,
    ConversationLifecycleMetadata,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    result_from_exception,
)
from ..tools.utils import (
    IMAGE_ATTACHMENT_EXTENSIONS,
    MAX_IMAGE_ATTACHMENT_BYTES,
    validate_local_file_path,
)
from .artifacts import artifact_from_local_path, artifact_from_remote, observed_backend_from_response
from .chat import ChatRequest, ChatService

logger = logging.getLogger(__name__)

UNDERSTAND_CLEANUP_SOURCE = "gemini_understand"
UNDERSTAND_IMAGE_CLEANUP_SOURCE = "gemini_understand_image"
DEFAULT_UNDERSTAND_IMAGE_TASK = "Describe what this image shows and call out anything noteworthy."
DEFAULT_IMAGE_INPUT_ID = "image"
MAX_UNDERSTAND_INPUTS = 16


class UnderstandingOutcome(str, Enum):
    """Truthful per-input outcome for one understanding request.

    ``SKIPPED`` inputs never reached the upstream request. ``ACCEPTED`` inputs
    were validated and included in the request, ``ANALYZED`` inputs additionally
    have a completed analysis acknowledging them — implicitly when they are the
    sole accepted input, otherwise only when the analysis references their
    ``[id]`` — and ``FAILED`` inputs were included in a request that errored.
    """

    ACCEPTED = "accepted"
    ANALYZED = "analyzed"
    SKIPPED = "skipped"
    FAILED = "failed"


class UnderstandInputKind(str, Enum):
    """Typed input kinds accepted by one mixed-input understanding request."""

    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    URL = "url"


@dataclass(frozen=True)
class UnderstandInput:
    """One typed understanding input with a caller-owned stable identity."""

    id: str
    kind: UnderstandInputKind
    path: str | None = None
    url: str | None = None
    text: str | None = None


@dataclass(frozen=True)
class UnderstandImageRequest:
    image: str
    task: str | None = None
    model: str = "flash"
    thinking_level: str = "standard"


@dataclass(frozen=True)
class UnderstandRequest:
    task: str
    inputs: tuple[UnderstandInput, ...] = ()
    model: str = "flash"
    thinking_level: str = "standard"
    cleanup_source: str = UNDERSTAND_CLEANUP_SOURCE


@dataclass(frozen=True)
class UnderstandInputOutcome:
    """Per-input outcome that keeps the caller-supplied identity stable."""

    id: str
    kind: str
    outcome: UnderstandingOutcome
    detail: str = ""
    artifact: Artifact | None = None


@dataclass(frozen=True)
class UnderstandOperationData:
    task: str
    analysis: str = ""
    inputs: tuple[UnderstandInputOutcome, ...] = ()
    requested_model: str | None = None
    effective_model: str | None = None
    observed_backend: str | None = None
    lifecycle: ConversationLifecycleMetadata | None = None


@dataclass(frozen=True)
class _PreparedInput:
    """One accepted input resolved into its chat-request contribution."""

    input_id: str
    reference: str
    file_path: str | None = None
    inline_text: str | None = None


class UnderstandService:
    """Own mixed-input understanding composition below the MCP presentation layers.

    The service reuses the shared :class:`~src.services.chat.ChatService` for
    request construction, execution, and cleanup scheduling: local image and
    file inputs ride the existing chat ``files`` upload workflow, remote
    image/file URIs and web URLs are referenced as content sources in the
    prompt, and inline text is embedded in the message. Every input keeps its
    caller-supplied ``id`` and gets one recorded outcome — ``accepted``,
    ``analyzed``, ``skipped``, or ``failed`` — so inputs are never silently
    dropped.
    """

    def __init__(self, chat_service: ChatService):
        self._chat_service = chat_service

    async def understand_image(self, request: UnderstandImageRequest) -> DomainResult[UnderstandOperationData]:
        image = request.image.strip()
        if not image:
            return _input_rejected("image must not be blank.")
        single_input = (
            UnderstandInput(id=DEFAULT_IMAGE_INPUT_ID, kind=UnderstandInputKind.IMAGE, url=image)
            if _is_http_url(image)
            else UnderstandInput(id=DEFAULT_IMAGE_INPUT_ID, kind=UnderstandInputKind.IMAGE, path=image)
        )
        task = (request.task or "").strip() or DEFAULT_UNDERSTAND_IMAGE_TASK
        return await self.understand(
            UnderstandRequest(
                task=task,
                inputs=(single_input,),
                model=request.model,
                thinking_level=request.thinking_level,
                cleanup_source=UNDERSTAND_IMAGE_CLEANUP_SOURCE,
            )
        )

    async def understand(self, request: UnderstandRequest) -> DomainResult[UnderstandOperationData]:
        task = request.task.strip()
        if not task:
            return _input_rejected("task must not be blank.")
        if not request.inputs:
            return _input_rejected("inputs must not be empty.")

        outcomes: list[UnderstandInputOutcome] = []
        prepared: list[_PreparedInput] = []
        seen_ids: set[str] = set()
        for index, raw_input in enumerate(request.inputs):
            outcome, contribution = _prepare_input(
                raw_input,
                ordinal=index,
                seen_ids=seen_ids,
                requested_model=request.model,
            )
            outcomes.append(outcome)
            if contribution is not None:
                prepared.append(contribution)

        if not prepared:
            return DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "No usable inputs were provided; every input was skipped.",
                data=UnderstandOperationData(task=task, inputs=tuple(outcomes), requested_model=request.model),
                suggested_action="Fix the reported inputs (paths, URLs, or text) and retry.",
                verification_status="input_rejected",
                details=_operation_details(outcomes),
            )

        try:
            chat_result = await self._chat_service.generate(
                ChatRequest(
                    message=_compose_understand_message(task, prepared),
                    model=request.model,
                    thinking_level=request.thinking_level,
                    files=tuple(item.file_path for item in prepared if item.file_path is not None),
                    cleanup_source=request.cleanup_source,
                    include_gem_argument=False,
                    include_temporary_argument=False,
                )
            )
        except Exception as error:
            return self._failed_from_exception(error, request, task, outcomes)

        if not chat_result.ok:
            failure = chat_result.error
            assert failure is not None
            return _understand_failure(
                task,
                outcomes,
                failure.code,
                failure.message,
                requested_model=request.model,
                retryable=failure.retryable,
                suggested_action=failure.suggested_action,
                operation_state=chat_result.meta.operation_state,
                request_id=chat_result.meta.request_id,
                diagnostic_id=chat_result.meta.diagnostic_id,
                verification_status=chat_result.meta.verification_status,
            )

        chat_data = chat_result.data
        assert chat_data is not None
        analysis = _response_text(chat_data.response).strip()
        final_outcomes = _with_final_outcomes(outcomes, analysis=analysis)
        warnings = _skip_warnings(final_outcomes)
        if analysis:
            warnings += _acknowledgment_warnings(final_outcomes)
        return DomainResult.success(
            UnderstandOperationData(
                task=task,
                analysis=analysis,
                inputs=final_outcomes,
                requested_model=chat_data.requested_model,
                effective_model=chat_data.effective_model,
                observed_backend=observed_backend_from_response(chat_data.response),
                lifecycle=chat_data.lifecycle,
            ),
            warnings=warnings,
            request_id=chat_result.meta.request_id,
            requested_backend=chat_data.requested_model,
            effective_backend=chat_data.effective_model,
            verification_status=chat_result.meta.verification_status,
            details=_operation_details(final_outcomes),
        )

    def _failed_from_exception(
        self,
        error: BaseException,
        request: UnderstandRequest,
        task: str,
        outcomes: Sequence[UnderstandInputOutcome],
    ) -> DomainResult[UnderstandOperationData]:
        classified = result_from_exception(error, logger=logger, operation=request.cleanup_source)
        failure = classified.error
        assert failure is not None
        return _understand_failure(
            task,
            outcomes,
            failure.code,
            failure.message,
            requested_model=request.model,
            retryable=failure.retryable,
            suggested_action=failure.suggested_action,
            operation_state=classified.meta.operation_state,
            request_id=classified.meta.request_id,
            diagnostic_id=classified.meta.diagnostic_id,
            verification_status=classified.meta.verification_status,
        )


def _prepare_input(
    raw_input: UnderstandInput,
    *,
    ordinal: int,
    seen_ids: set[str],
    requested_model: str,
) -> tuple[UnderstandInputOutcome, _PreparedInput | None]:
    """Validate one typed input into an outcome plus its chat contribution."""

    input_id = (raw_input.id or "").strip()
    kind_label = _kind_label(raw_input.kind)
    if not input_id:
        return _skipped_outcome(raw_input, "input id must not be blank."), None
    if input_id in seen_ids:
        return (
            _skipped_outcome(
                raw_input,
                f"duplicate input id {input_id!r}; ids must stay unique so outcomes keep one identity per input.",
            ),
            None,
        )
    if ordinal >= MAX_UNDERSTAND_INPUTS:
        return _skipped_outcome(raw_input, f"input limit exceeded (max {MAX_UNDERSTAND_INPUTS} inputs)."), None
    seen_ids.add(input_id)

    try:
        kind = UnderstandInputKind(raw_input.kind)
    except ValueError:
        return _skipped_outcome(raw_input, f"unsupported input kind {kind_label!r}."), None

    if kind is UnderstandInputKind.TEXT:
        text = (raw_input.text or "").strip()
        if not text:
            return _skipped_outcome(raw_input, "text input must not be blank."), None
        return (
            _accepted_outcome(raw_input, input_id, kind_label, detail=""),
            _PreparedInput(
                input_id=input_id,
                reference=f"[{input_id}] text provided inline below",
                inline_text=text,
            ),
        )

    if kind is UnderstandInputKind.URL:
        url = (raw_input.url or "").strip()
        if not _is_http_url(url):
            return _skipped_outcome(raw_input, "url input must be an absolute http(s) URL."), None
        return (
            _accepted_outcome(
                raw_input,
                input_id,
                kind_label,
                detail=url,
                artifact=artifact_from_remote(
                    ArtifactKind.WEBPAGE,
                    url,
                    title=urlsplit(url).netloc or None,
                    requested_backend=requested_model,
                    verification_method="input_uri_provided",
                ),
            ),
            _PreparedInput(
                input_id=input_id,
                reference=f"[{input_id}] url {url} (use the URL as the content source)",
            ),
        )

    location = (raw_input.path or "").strip() or (raw_input.url or "").strip()
    if not location:
        return _skipped_outcome(raw_input, f"{kind.value} input requires a path or url."), None

    if _is_http_url(location):
        return (
            _accepted_outcome(
                raw_input,
                input_id,
                kind_label,
                detail=location,
                artifact=artifact_from_remote(
                    _artifact_kind(kind),
                    location,
                    title=urlsplit(location).netloc or None,
                    requested_backend=requested_model,
                    verification_method="input_uri_provided",
                ),
            ),
            _PreparedInput(
                input_id=input_id,
                reference=f"[{input_id}] {kind.value} at {location} (use the URL as the content source)",
            ),
        )

    if kind is UnderstandInputKind.IMAGE:
        valid, value = validate_local_file_path(
            location,
            allowed_extensions=IMAGE_ATTACHMENT_EXTENSIONS,
            max_bytes=MAX_IMAGE_ATTACHMENT_BYTES,
        )
    else:
        valid, value = validate_local_file_path(location)
    if not valid:
        return _skipped_outcome(raw_input, f"local {kind.value} rejected: {value}"), None
    if _is_empty_local_file(value):
        return _skipped_outcome(raw_input, f"local {kind.value} is empty (0 bytes); provide a non-empty file."), None
    return (
        _accepted_outcome(
            raw_input,
            input_id,
            kind_label,
            detail=value,
            artifact=artifact_from_local_path(
                _artifact_kind(kind),
                value,
                title=Path(value).name,
                requested_backend=requested_model,
            ),
        ),
        _PreparedInput(
            input_id=input_id,
            reference=f"[{input_id}] {kind.value} attached to this message",
            file_path=value,
        ),
    )


def _compose_understand_message(task: str, prepared: Sequence[_PreparedInput]) -> str:
    lines = [
        "Analyze the inputs below and complete the task.",
        "",
        f"Task: {task}",
        "",
        "Inputs:",
    ]
    lines.extend(f"- {item.reference}" for item in prepared)
    has_inline = any(item.inline_text for item in prepared)
    if has_inline:
        lines.append("")
        lines.append("Inline text:")
        block_index = 0
        for item in prepared:
            inline_text = item.inline_text
            if not inline_text:
                continue
            if block_index:
                lines.append("")
            lines.append(f"[{item.input_id}]")
            lines.append(inline_text)
            block_index += 1
    lines.append("")
    lines.append("Refer to every input by its [id] so each observation stays tied to its source.")
    return "\n".join(lines)


def _accepted_outcome(
    raw_input: UnderstandInput,
    input_id: str,
    kind_label: str,
    *,
    detail: str,
    artifact: Artifact | None = None,
) -> UnderstandInputOutcome:
    return UnderstandInputOutcome(
        id=input_id,
        kind=kind_label,
        outcome=UnderstandingOutcome.ACCEPTED,
        detail=detail,
        artifact=artifact,
    )


def _skipped_outcome(raw_input: UnderstandInput, reason: str) -> UnderstandInputOutcome:
    return UnderstandInputOutcome(
        id=(raw_input.id or "").strip(),
        kind=_kind_label(raw_input.kind),
        outcome=UnderstandingOutcome.SKIPPED,
        detail=reason,
    )


def _with_final_outcomes(
    outcomes: Sequence[UnderstandInputOutcome],
    *,
    analysis: str,
) -> tuple[UnderstandInputOutcome, ...]:
    """Upgrade accepted inputs once the analysis outcome is known.

    A completed non-empty analysis may mark the sole accepted input analyzed.
    With multiple accepted inputs, only inputs the analysis actually references
    by ``[id]`` are marked analyzed; the rest stay accepted because individual
    acknowledgment was not observed.
    """

    if not analysis:
        return tuple(outcomes)
    accepted = [outcome for outcome in outcomes if outcome.outcome is UnderstandingOutcome.ACCEPTED]
    if not accepted:
        return tuple(outcomes)
    sole_accepted = len(accepted) == 1
    return tuple(
        replace(outcome, outcome=UnderstandingOutcome.ANALYZED)
        if outcome.outcome is UnderstandingOutcome.ACCEPTED
        and (sole_accepted or f"[{outcome.id}]" in analysis)
        else outcome
        for outcome in outcomes
    )


def _with_failed_outcomes(
    outcomes: Sequence[UnderstandInputOutcome],
) -> tuple[UnderstandInputOutcome, ...]:
    return tuple(
        replace(outcome, outcome=UnderstandingOutcome.FAILED)
        if outcome.outcome is UnderstandingOutcome.ACCEPTED
        else outcome
        for outcome in outcomes
    )


def _skip_warnings(outcomes: Sequence[UnderstandInputOutcome]) -> tuple[DomainWarning, ...]:
    return tuple(
        DomainWarning(
            code="input_skipped",
            message=f"Input [{outcome.id or '(missing id)'}] was skipped: {outcome.detail}",
            suggested_action="Fix or remove the skipped input and retry.",
        )
        for outcome in outcomes
        if outcome.outcome is UnderstandingOutcome.SKIPPED
    )


def _acknowledgment_warnings(outcomes: Sequence[UnderstandInputOutcome]) -> tuple[DomainWarning, ...]:
    return tuple(
        DomainWarning(
            code="input_acknowledgment_not_observed",
            message=(
                f"Input [{outcome.id or '(missing id)'}] was included in the completed request; "
                "individual acknowledgment not observed in the analysis."
            ),
            suggested_action="Re-run the task and ask for observations that reference every input by its [id].",
        )
        for outcome in outcomes
        if outcome.outcome is UnderstandingOutcome.ACCEPTED
    )


def _operation_details(outcomes: Sequence[UnderstandInputOutcome]) -> dict[str, Any]:
    return {
        "service": "understanding",
        "accepted": sum(outcome.outcome is UnderstandingOutcome.ACCEPTED for outcome in outcomes),
        "analyzed": sum(outcome.outcome is UnderstandingOutcome.ANALYZED for outcome in outcomes),
        "skipped": sum(outcome.outcome is UnderstandingOutcome.SKIPPED for outcome in outcomes),
        "failed": sum(outcome.outcome is UnderstandingOutcome.FAILED for outcome in outcomes),
    }


def _understand_failure(
    task: str,
    outcomes: Sequence[UnderstandInputOutcome],
    code: DomainErrorCode,
    message: str,
    *,
    requested_model: str | None = None,
    retryable: bool = False,
    suggested_action: str | None = None,
    operation_state: OperationState = OperationState.FAILED,
    request_id: str | None = None,
    diagnostic_id: str | None = None,
    verification_status: str = "not_applicable",
) -> DomainResult[UnderstandOperationData]:
    failed = _with_failed_outcomes(outcomes)
    return DomainResult.failure(
        code,
        message,
        data=UnderstandOperationData(
            task=task,
            analysis="",
            inputs=failed,
            requested_model=requested_model,
        ),
        retryable=retryable,
        suggested_action=suggested_action,
        operation_state=operation_state,
        request_id=request_id,
        diagnostic_id=diagnostic_id,
        requested_backend=requested_model,
        verification_status=verification_status,
        details=_operation_details(failed),
    )


def _input_rejected(message: str) -> DomainResult[UnderstandOperationData]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
        details={"service": "understanding"},
    )


def _kind_label(kind: UnderstandInputKind | str) -> str:
    return kind.value if isinstance(kind, UnderstandInputKind) else str(kind)


def _artifact_kind(kind: UnderstandInputKind) -> ArtifactKind:
    if kind is UnderstandInputKind.IMAGE:
        return ArtifactKind.IMAGE
    return ArtifactKind.FILE


def _response_text(response: Any) -> str:
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""


def _is_empty_local_file(path: str) -> bool:
    try:
        return Path(path).stat().st_size == 0
    except OSError:
        return False


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
