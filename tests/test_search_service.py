"""Shared SearchService grounded-search contract tests.

The search service owns the grounded-search contract for every surface:
``grounded`` requires observed source evidence, source-free prose stays
``answer_only``, an empty answer is ``unavailable``, and an errored attempt is
``failed``. These tests pin the classification, source observation, request
delegation, and typed input rejection without any MCP presentation layer.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace

from src.domain import DomainErrorCode, DomainResult
from src.services import (
    ChatOperationData,
    ChatRequest,
    GroundingState,
    ObservedSource,
    SearchRequest,
    SearchService,
    observed_sources_from_response,
)


def _response(text="answer text", citations=()):
    return SimpleNamespace(
        text=text,
        images=[],
        videos=[],
        media=[],
        citations=list(citations),
        observed_backend="gemini-3-flash",
        metadata=["c_search_svc", "r_response"],
    )


class _StubChatService:
    """Minimal ChatService stand-in; SearchService only calls generate()."""

    def __init__(self, *, response=None, error=None, failure=None):
        self._response = response
        self._error = error
        self._failure = failure
        self.captured_request: ChatRequest | None = None

    async def generate(self, request: ChatRequest) -> DomainResult[ChatOperationData]:
        self.captured_request = request
        if self._error is not None:
            raise self._error
        if self._failure is not None:
            return self._failure
        return DomainResult.success(
            ChatOperationData(
                requested_model=request.model,
                normalized_model=request.model,
                effective_model="gemini-3-flash",
                response=self._response,
            ),
            requested_backend=request.model,
            effective_backend="gemini-3-flash",
            verification_status="upstream_response_received",
        )


def _search(stub: _StubChatService, **kwargs):
    return asyncio.run(SearchService(stub).search(SearchRequest(**kwargs)))


# ---------------------------------------------------------------------------
# Delegation to the shared ChatService
# ---------------------------------------------------------------------------


def test_search_reuses_the_shared_chat_service_request_shape():
    stub = _StubChatService(response=_response())

    result = _search(stub, query="latest release notes", model="lite")

    assert result.ok is True
    request = stub.captured_request
    assert request is not None
    assert request.model == "lite"
    assert request.thinking_level == "standard"
    assert request.cleanup_source == "gemini_search"
    assert request.include_gem_argument is False
    assert request.include_temporary_argument is False
    assert request.message.startswith("Search the web for current information")
    assert "Question: latest release notes" in request.message


def test_search_composes_optional_constraints_into_the_message():
    stub = _StubChatService(response=_response())

    _search(
        stub,
        query="release date",
        recency="30 days",
        domains=["Example.com", " example.com ", ""],
        language="Japanese",
    )

    message = stub.captured_request.message
    assert "last 30 days" in message
    assert "example.com" in message
    assert message.count("example.com") == 1
    assert "Answer language: Japanese" in message


def test_search_omits_constraints_that_were_not_provided():
    stub = _StubChatService(response=_response())

    _search(stub, query="plain query")

    message = stub.captured_request.message
    assert "Recency:" not in message
    assert "Preferred source domains:" not in message
    assert "Answer language:" not in message


# ---------------------------------------------------------------------------
# Grounding classification
# ---------------------------------------------------------------------------


def test_search_is_grounded_only_with_observed_source_evidence():
    citations = [
        SimpleNamespace(url="https://example.com/one", title="One"),
        SimpleNamespace(url="https://example.com/two", title=None),
    ]
    stub = _StubChatService(response=_response(text="sourced answer", citations=citations))

    result = _search(stub, query="anything")

    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.grounding_state is GroundingState.GROUNDED
    assert data.answer == "sourced answer"
    assert data.sources == (
        ObservedSource(url="https://example.com/one", title="One"),
        ObservedSource(url="https://example.com/two", title=None),
    )
    assert data.observed_at
    datetime.fromisoformat(data.observed_at)
    assert data.requested_model == "flash"
    assert data.effective_model == "gemini-3-flash"
    assert data.observed_backend == "gemini-3-flash"
    assert data.lifecycle is not None
    assert result.meta.details["grounding_state"] == "grounded"


def test_search_classifies_source_free_prose_as_answer_only():
    stub = _StubChatService(response=_response(text="unsourced prose", citations=[]))

    result = _search(stub, query="anything")

    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.grounding_state is GroundingState.ANSWER_ONLY
    assert data.sources == ()
    assert data.answer == "unsourced prose"


def test_search_reports_unavailable_when_the_answer_is_empty():
    stub = _StubChatService(response=_response(text="   ", citations=[]))

    result = _search(stub, query="anything")

    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.grounding_state is GroundingState.UNAVAILABLE
    assert data.answer == ""


def test_search_marks_errored_attempts_as_failed_with_typed_error():
    stub = _StubChatService(error=RuntimeError("upstream exploded"))

    result = _search(stub, query="anything")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.INTERNAL_ERROR
    assert result.data is not None
    assert result.data.grounding_state is GroundingState.FAILED
    assert result.data.sources == ()
    assert result.meta.verification_status == "exception_classified"
    assert result.meta.details["grounding_state"] == "failed"


def test_search_preserves_chat_failure_errors_as_failed():
    chat_failure = DomainResult.failure(
        DomainErrorCode.NETWORK_ERROR,
        "The upstream service could not be reached.",
        retryable=True,
        suggested_action="Check network or proxy settings and retry.",
    )
    stub = _StubChatService(failure=chat_failure)

    result = _search(stub, query="anything")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.NETWORK_ERROR
    assert result.error.retryable is True
    assert result.data is not None
    assert result.data.grounding_state is GroundingState.FAILED


# ---------------------------------------------------------------------------
# Bounded source reporting and input rejection
# ---------------------------------------------------------------------------


def test_search_caps_sources_at_max_results_and_warns_on_truncation():
    citations = [
        {"url": f"https://example.com/{index}", "title": None} for index in range(3)
    ]
    stub = _StubChatService(response=_response(citations=citations))

    result = _search(stub, query="anything", max_results=2)

    assert result.ok is True
    data = result.data
    assert data is not None
    assert [source.url for source in data.sources] == [
        "https://example.com/0",
        "https://example.com/1",
    ]
    assert [warning.code for warning in result.warnings] == ["sources_truncated"]
    assert "Observed 3 sources" in result.warnings[0].message


def test_search_does_not_warn_when_all_sources_fit():
    citations = [{"url": "https://example.com/1", "title": None}]
    stub = _StubChatService(response=_response(citations=citations))

    result = _search(stub, query="anything", max_results=5)

    assert result.ok is True
    assert result.warnings == ()


def test_search_observed_source_count_reports_the_total_when_truncated():
    citations = [
        {"url": f"https://example.com/{index}", "title": None} for index in range(3)
    ]
    stub = _StubChatService(response=_response(citations=citations))

    result = _search(stub, query="anything", max_results=2)

    assert result.ok is True
    data = result.data
    assert data is not None
    assert len(data.sources) == 2
    # The count reports every observed source, not just the returned prefix.
    assert data.observed_source_count == 3


def test_search_observed_source_count_equals_the_returned_sources_when_not_truncated():
    citations = [
        {"url": f"https://example.com/{index}", "title": None} for index in range(2)
    ]
    stub = _StubChatService(response=_response(citations=citations))

    result = _search(stub, query="anything", max_results=8)

    assert result.ok is True
    data = result.data
    assert data is not None
    assert len(data.sources) == 2
    assert data.observed_source_count == len(data.sources)


def test_search_rejects_blank_query_before_any_client_use():
    stub = _StubChatService(response=_response())

    result = _search(stub, query="   ")

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.INVALID_ARGUMENT
    assert result.data is None
    assert result.meta.verification_status == "input_rejected"
    assert stub.captured_request is None


def test_search_rejects_non_positive_max_results():
    stub = _StubChatService(response=_response())

    result = _search(stub, query="anything", max_results=0)

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.INVALID_ARGUMENT
    assert stub.captured_request is None


# ---------------------------------------------------------------------------
# Observed-source extraction
# ---------------------------------------------------------------------------


def test_observed_sources_from_response_keeps_only_unique_http_urls():
    response = _response(
        citations=[
            SimpleNamespace(url="https://example.com/dup", title="First"),
            {"url": "https://example.com/dup", "title": "Duplicate"},
            {"url": " https://example.com/padded ", "title": " Padded "},
            {"url": None, "title": "No URL"},
            {"url": "javascript:alert(1)", "title": "Dangerous"},
            {"url": "example.com/no-scheme", "title": "No Scheme"},
            {"url": "https://", "title": "No Host"},
        ]
    )

    sources = observed_sources_from_response(response)

    assert sources == (
        ObservedSource(url="https://example.com/dup", title="First"),
        ObservedSource(url="https://example.com/padded", title="Padded"),
    )


def test_observed_sources_from_response_tolerates_missing_citations():
    assert observed_sources_from_response(_response(citations=[])) == ()
    assert observed_sources_from_response(SimpleNamespace(text="answer")) == ()
