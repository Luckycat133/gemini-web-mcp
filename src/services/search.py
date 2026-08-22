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
from .artifacts import observed_backend_from_response
from .chat import ChatRequest, ChatService

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
    """One observed source URL that backed the answer."""

    url: str
    title: str | None = None


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


class SearchService:
    """Own grounded-search composition below the MCP presentation layers.

    The service reuses the shared :class:`~src.services.chat.ChatService` for
    request construction, execution, and cleanup scheduling, then derives the
    grounding contract from what the upstream response actually observed:
    ``grounded`` only when at least one source URL was observed, otherwise
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
            chat_result = await self._chat_service.generate(
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
        answer = _answer_text(response).strip()
        observed = observed_sources_from_response(response)
        sources = observed[: request.max_results]
        warnings: list[DomainWarning] = []
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


def observed_sources_from_response(response: Any) -> tuple[ObservedSource, ...]:
    """Collect deduplicated http(s) source URLs observed on one upstream response."""
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
    return tuple(sources)


def _classify_grounding(answer: str, sources: Sequence[ObservedSource]) -> GroundingState:
    if not answer.strip():
        return GroundingState.UNAVAILABLE
    if sources:
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
