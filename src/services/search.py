"""Shared grounded-search application service for assistance surfaces."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from ..domain import (
    ConversationLifecycleMetadata,
    DomainErrorCode,
    DomainResult,
    DomainWarning,
    OperationState,
    result_from_exception,
)
from ..infrastructure.rpc_parsers import CandidateSourceEvidence, attribution_matches_answer
from .artifacts import observed_backend_from_response
from .chat import ChatRequest, ChatService
from .model_policy import ModelSelection

logger = logging.getLogger(__name__)

SEARCH_CLEANUP_SOURCE = "gemini_search"
# Distinct from SEARCH_CLEANUP_SOURCE: this label identifies the failed search
# execution in exception logs instead of reusing the remote-cleanup source name.
SEARCH_EXCEPTION_OPERATION = "gemini_search_execution"
DEFAULT_MAX_RESULTS = 8


class GroundingState(str, Enum):
    """Truthful grounding classification for one search result.

    ``GROUNDED`` requires observed source evidence. A model answer without
    evidence is ``ANSWER_ONLY`` and must never be labeled grounded.
    """

    GROUNDED = "grounded"
    ANSWER_ONLY = "answer_only"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class ObservedSource:
    """A source URL with its observed attribution or retrieval evidence role."""

    url: str
    title: str | None = None
    role: str = "citation"


@dataclass(frozen=True)
class SearchRequest:
    query: str
    recency: str | None = None
    domains: tuple[str, ...] = ()
    language: str | None = None
    max_results: int = DEFAULT_MAX_RESULTS
    model: str = "flash"
    thinking_level: str = "standard"


@dataclass(frozen=True)
class SearchOperationData:
    query: str
    grounding_state: GroundingState
    answer: str = ""
    sources: tuple[ObservedSource, ...] = ()
    observed_at: str | None = None
    observed_source_count: int | None = None
    requested_model: str | None = None
    effective_model: str | None = None
    observed_backend: str | None = None
    lifecycle: ConversationLifecycleMetadata | None = None
    model_selection: ModelSelection | None = None


@dataclass(frozen=True)
class ResponseEvidence:
    answer: str
    grounding_state: GroundingState
    sources: tuple[ObservedSource, ...]
    observed_source_count: int
    observed_at: str | None


def response_evidence(responses: Sequence[Any], response: Any, *, stream_text: str = "",
                      observed_at: str | None = None) -> ResponseEvidence:
    """Describe answer and structured sources observed for the selected reply."""
    answer = (_answer_text(response) or stream_text).strip()
    sources = observed_sources_from_frames(responses, response)
    return ResponseEvidence(answer, _classify_grounding(answer, sources), sources, len(sources), observed_at)


class SearchService:
    """Own grounded-search composition below the MCP presentation layers.

    The service reuses the shared :class:`~src.services.chat.ChatService` for
    request construction, execution, and cleanup scheduling, then derives the
    grounding contract from what the upstream response actually observed:
    ``grounded`` only when at least one structured citation URL was observed.
    Links written in model prose are not retrieval evidence. Otherwise use
    ``answer_only`` for prose, ``unavailable`` for an empty answer, and
    ``failed`` when the attempted search errored.
    """

    def __init__(self, chat_service: ChatService):
        self._chat_service = chat_service

    async def search(self, request: SearchRequest) -> DomainResult[SearchOperationData]:
        query = request.query.strip()
        if not query:
            return _input_rejected("query must not be blank.")
        if request.max_results < 1:
            return _input_rejected("max_results must be a positive integer.")

        domains = _normalize_domains(request.domains)
        try:
            chat_result = await self._chat_service.generate_stream(
                ChatRequest(
                    message=_compose_search_message(query, request, domains),
                    model=request.model,
                    thinking_level=request.thinking_level,
                    cleanup_source=SEARCH_CLEANUP_SOURCE,
                    include_gem_argument=False,
                    include_temporary_argument=False,
                )
            )
        except Exception as error:
            return self._failed_from_exception(error, request)

        if not chat_result.ok:
            failure = chat_result.error
            assert failure is not None
            return _search_failure(
                request,
                failure.code,
                failure.message,
                retryable=failure.retryable,
                suggested_action=failure.suggested_action,
                operation_state=chat_result.meta.operation_state,
                request_id=chat_result.meta.request_id,
                diagnostic_id=chat_result.meta.diagnostic_id,
                verification_status=chat_result.meta.verification_status,
            )

        chat_data = chat_result.data
        assert chat_data is not None
        response = chat_data.response
        evidence = response_evidence(chat_data.responses, response, stream_text=chat_data.stream_text,
                                     observed_at=chat_result.meta.observed_at)
        answer = evidence.answer
        observed = evidence.sources
        sources = observed[: request.max_results]
        warnings: list[DomainWarning] = list(chat_result.warnings)
        if len(observed) > len(sources):
            warnings.append(
                DomainWarning(
                    code="sources_truncated",
                    message=(
                        f"Observed {len(observed)} sources; returning the first {len(sources)} "
                        f"(max_results={request.max_results})."
                    ),
                    suggested_action="Increase max_results to see the remaining sources.",
                )
            )
        grounding_state = _classify_grounding(answer, sources)
        if any(source.role == "retrieved_only" for source in sources):
            warnings.append(DomainWarning(
                code="retrieved_sources_unverified",
                message="Retrieved sources were observed, but their support for the answer is unverified.",
            ))
        return DomainResult.success(
            SearchOperationData(
                query=query,
                grounding_state=grounding_state,
                answer=answer,
                sources=sources,
                observed_at=chat_result.meta.observed_at,
                observed_source_count=len(observed),
                requested_model=chat_data.requested_model,
                effective_model=chat_data.effective_model,
                observed_backend=observed_backend_from_response(response),
                lifecycle=chat_data.lifecycle,
                model_selection=chat_data.model_selection,
            ),
            warnings=warnings,
            request_id=chat_result.meta.request_id,
            requested_backend=chat_data.requested_model,
            effective_backend=chat_data.effective_model,
            verification_status=chat_result.meta.verification_status,
            details={"service": "search", "grounding_state": grounding_state.value},
        )

    def _failed_from_exception(
        self,
        error: BaseException,
        request: SearchRequest,
    ) -> DomainResult[SearchOperationData]:
        classified = result_from_exception(error, logger=logger, operation=SEARCH_EXCEPTION_OPERATION)
        failure = classified.error
        assert failure is not None
        return _search_failure(
            request,
            failure.code,
            failure.message,
            retryable=failure.retryable,
            suggested_action=failure.suggested_action,
            operation_state=classified.meta.operation_state,
            request_id=classified.meta.request_id,
            diagnostic_id=classified.meta.diagnostic_id,
            verification_status=classified.meta.verification_status,
        )


def observed_sources_from_response(response: Any, *, final_answer: str | None = None) -> tuple[ObservedSource, ...]:
    """Collect structured citations, never treating model-written links as grounding."""
    sources: list[ObservedSource] = []
    seen: set[str] = set()
    for citation in getattr(response, "citations", None) or ():
        url = _citation_field(citation, "url")
        if not isinstance(url, str):
            continue
        url = url.strip()
        if not _is_http_url(url) or url in seen:
            continue
        seen.add(url)
        title = _citation_field(citation, "title")
        title_text = title.strip() if isinstance(title, str) else ""
        sources.append(ObservedSource(url=url, title=title_text or None))
    candidates = getattr(response, "candidates", ())
    chosen = getattr(response, "chosen", 0)
    if isinstance(candidates, (list, tuple)) and isinstance(chosen, int) and 0 <= chosen < len(candidates):
        for item in getattr(candidates[chosen], "_retrieved_sources", ()):
            if not isinstance(item, CandidateSourceEvidence):
                continue
            if not _is_http_url(item.url) or item.url in seen:
                continue
            seen.add(item.url)
            current_answer = _answer_text(response) or ""
            target_answer = current_answer if final_answer is None else final_answer
            verified = attribution_matches_answer(item, current_answer) and attribution_matches_answer(item, target_answer)
            role = "source_attribution" if verified else "retrieved_only"
            sources.append(ObservedSource(url=item.url, title=item.title, role=role))
    return tuple(sources)


def observed_sources_from_frames(responses: Sequence[Any], final_response: Any) -> tuple[ObservedSource, ...]:
    """Preserve structured citations for the same selected reply across stream frames.

    The SDK rebuilds candidates independently per frame and its non-streaming
    helper returns only the last frame. Earlier evidence is accepted only when
    both upstream conversation/turn identity and selected candidate identity
    match the final response. Missing identity never broadens attribution.
    """
    anchor = _reply_identity(final_response)
    if anchor is None:
        return observed_sources_from_response(final_response)
    sources: list[ObservedSource] = []
    seen: set[str] = set()
    for frame in (*responses, final_response):
        if _reply_identity(frame) != anchor:
            continue
        for source in observed_sources_from_response(frame, final_answer=_answer_text(final_response) or ""):
            if source.url not in seen:
                seen.add(source.url)
                sources.append(source)
            elif source.role in {"citation", "source_attribution"}:
                for index, previous in enumerate(sources):
                    if previous.url == source.url and previous.role == "retrieved_only":
                        sources[index] = source
                        break
    return tuple(sources)


def _reply_identity(response: Any) -> tuple[str, str, str] | None:
    metadata = getattr(response, "metadata", None)
    if not isinstance(metadata, (list, tuple)) or len(metadata) < 2:
        return None
    cid, rid = metadata[:2]
    rcid = getattr(response, "rcid", None)
    if not isinstance(cid, str) or not isinstance(rid, str) or not isinstance(rcid, str):
        return None
    if not cid.strip() or not rid.strip() or not rcid.strip():
        return None
    return cid, rid, rcid


def _classify_grounding(answer: str, sources: Sequence[ObservedSource]) -> GroundingState:
    if not answer.strip():
        return GroundingState.UNAVAILABLE
    if any(source.role in {"citation", "source_attribution"} for source in sources):
        return GroundingState.GROUNDED
    return GroundingState.ANSWER_ONLY


def _compose_search_message(query: str, request: SearchRequest, domains: Sequence[str]) -> str:
    lines = [
        "Search the web for current information and answer the question below.",
        "Cite every source you relied on so each claim can be checked.",
        "",
        f"Question: {query}",
    ]
    constraints: list[str] = []
    recency = (request.recency or "").strip()
    if recency:
        constraints.append(f"Recency: prefer sources from the last {recency}.")
    if domains:
        constraints.append(f"Preferred source domains: {', '.join(domains)}.")
    language = (request.language or "").strip()
    if language:
        constraints.append(f"Answer language: {language}.")
    if constraints:
        lines.append("")
        lines.extend(constraints)
    return "\n".join(lines)


def _normalize_domains(domains: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for domain in domains:
        value = str(domain).strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _answer_text(response: Any) -> str:
    text = getattr(response, "text", "")
    return text if isinstance(text, str) else ""


def _citation_field(citation: Any, name: str) -> Any:
    if isinstance(citation, Mapping):
        return citation.get(name)
    return getattr(citation, name, None)


def _is_http_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)


def _input_rejected(message: str) -> DomainResult[SearchOperationData]:
    return DomainResult.failure(
        DomainErrorCode.INVALID_ARGUMENT,
        message,
        suggested_action="Correct the arguments and retry.",
        verification_status="input_rejected",
        details={"service": "search"},
    )


def _search_failure(
    request: SearchRequest,
    code: DomainErrorCode,
    message: str,
    *,
    retryable: bool = False,
    suggested_action: str | None = None,
    operation_state: OperationState = OperationState.FAILED,
    request_id: str | None = None,
    diagnostic_id: str | None = None,
    verification_status: str = "not_applicable",
) -> DomainResult[SearchOperationData]:
    return DomainResult.failure(
        code,
        message,
        data=SearchOperationData(query=request.query.strip(), grounding_state=GroundingState.FAILED),
        retryable=retryable,
        suggested_action=suggested_action,
        operation_state=operation_state,
        request_id=request_id,
        diagnostic_id=diagnostic_id,
        verification_status=verification_status,
        details={"service": "search", "grounding_state": GroundingState.FAILED.value},
    )
