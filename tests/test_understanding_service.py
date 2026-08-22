"""Shared UnderstandService mixed-input contract tests.

The understanding service owns the per-input contract for every surface:
typed inputs keep their caller-supplied identities, local image/file inputs
ride the shared chat upload workflow, remote URIs and URLs are referenced as
content sources, inline text is embedded, and every input gets one recorded
outcome — ``accepted``, ``analyzed``, ``skipped``, or ``failed`` — so no
input is silently dropped. These tests pin request delegation, outcome
classification, identity preservation, and typed input rejection without any
MCP presentation layer.
"""

import asyncio
from types import SimpleNamespace

from src.domain import DomainErrorCode, DomainResult
from src.services import (
    ChatOperationData,
    ChatRequest,
    UnderstandImageRequest,
    UnderstandInput,
    UnderstandInputKind,
    UnderstandRequest,
    UnderstandService,
    UnderstandingOutcome,
)


def _response(text="analysis text"):
    return SimpleNamespace(
        text=text,
        images=[],
        videos=[],
        media=[],
        observed_backend="gemini-3-flash",
        metadata=["c_understand_svc", "r_response"],
    )


class _StubChatService:
    """Minimal ChatService stand-in; UnderstandService only calls generate()."""

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


def _understand(stub: _StubChatService, **kwargs):
    return asyncio.run(UnderstandService(stub).understand(UnderstandRequest(**kwargs)))


def _outcomes(result):
    assert result.data is not None
    return [(item.id, item.kind, item.outcome) for item in result.data.inputs]


# ---------------------------------------------------------------------------
# Delegation to the shared ChatService
# ---------------------------------------------------------------------------


def test_understand_reuses_the_shared_chat_service_request_shape(tmp_path):
    image = tmp_path / "design.png"
    image.write_bytes(b"fake image bytes")
    stub = _StubChatService(response=_response())

    result = _understand(
        stub,
        task="Compare the design with the implementation.",
        inputs=(UnderstandInput(id="design", kind=UnderstandInputKind.IMAGE, path=str(image)),),
        model="lite",
    )

    assert result.ok is True
    request = stub.captured_request
    assert request is not None
    assert request.model == "lite"
    assert request.thinking_level == "standard"
    assert request.cleanup_source == "gemini_understand"
    assert request.files == (str(image),)
    assert request.include_gem_argument is False
    assert request.include_temporary_argument is False
    assert request.message.startswith("Analyze the inputs below")
    assert "Task: Compare the design with the implementation." in request.message
    assert "[design] image attached to this message" in request.message


def test_understand_composes_each_input_kind_into_one_message(tmp_path):
    image = tmp_path / "design.png"
    image.write_bytes(b"fake image bytes")
    spec = tmp_path / "spec.md"
    spec.write_text("# spec")
    stub = _StubChatService(response=_response())

    _understand(
        stub,
        task="Compare everything.",
        inputs=(
            UnderstandInput(id="design", kind=UnderstandInputKind.IMAGE, path=str(image)),
            UnderstandInput(id="logo", kind=UnderstandInputKind.IMAGE, url="https://example.com/logo.png"),
            UnderstandInput(id="spec", kind=UnderstandInputKind.FILE, path=str(spec)),
            UnderstandInput(id="report", kind=UnderstandInputKind.FILE, url="https://example.com/report.pdf"),
            UnderstandInput(id="docs", kind=UnderstandInputKind.URL, url="https://example.com/docs"),
            UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="The button should be blue."),
        ),
    )

    request = stub.captured_request
    assert request is not None
    assert request.files == (str(image), str(spec))
    message = request.message
    assert "[design] image attached to this message" in message
    assert "[logo] image at https://example.com/logo.png (use the URL as the content source)" in message
    assert "[spec] file attached to this message" in message
    assert "[report] file at https://example.com/report.pdf (use the URL as the content source)" in message
    assert "[docs] url https://example.com/docs (use the URL as the content source)" in message
    assert "[notes] text provided inline below" in message
    assert "[notes]\nThe button should be blue." in message
    assert message.endswith("Refer to every input by its [id] so each observation stays tied to its source.")


def test_understand_image_delegates_with_default_task_and_cleanup_source(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"fake image bytes")
    stub = _StubChatService(response=_response())

    result = asyncio.run(
        UnderstandService(stub).understand_image(UnderstandImageRequest(image=str(image)))
    )

    assert result.ok is True
    request = stub.captured_request
    assert request is not None
    assert request.cleanup_source == "gemini_understand_image"
    assert request.files == (str(image),)
    assert "Task: " in request.message
    assert result.data is not None
    assert result.data.task
    assert _outcomes(result) == [("image", "image", UnderstandingOutcome.ANALYZED)]


def test_understand_image_remote_uri_is_referenced_not_uploaded():
    stub = _StubChatService(response=_response())

    result = asyncio.run(
        UnderstandService(stub).understand_image(
            UnderstandImageRequest(image="https://example.com/logo.png", task="Describe the logo.")
        )
    )

    assert result.ok is True
    request = stub.captured_request
    assert request is not None
    assert request.files == ()
    assert "Task: Describe the logo." in request.message
    assert "[image] image at https://example.com/logo.png" in request.message


# ---------------------------------------------------------------------------
# Per-input outcomes and identity preservation
# ---------------------------------------------------------------------------


def test_understand_marks_accepted_inputs_analyzed_after_a_completed_analysis():
    stub = _StubChatService(response=_response(text="synthesized analysis"))

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="some notes"),),
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.analysis == "synthesized analysis"
    assert _outcomes(result) == [("notes", "text", UnderstandingOutcome.ANALYZED)]
    assert result.meta.details == {
        "service": "understanding",
        "accepted": 0,
        "analyzed": 1,
        "skipped": 0,
        "failed": 0,
    }


def test_understand_keeps_accepted_state_when_no_analysis_is_returned():
    stub = _StubChatService(response=_response(text="   "))

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="some notes"),),
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data.analysis == ""
    # Without a completed analysis the input stays accepted, never analyzed.
    assert _outcomes(result) == [("notes", "text", UnderstandingOutcome.ACCEPTED)]


def test_understand_preserves_input_order_and_ids_with_mixed_outcomes(tmp_path):
    image = tmp_path / "design.png"
    image.write_bytes(b"fake image bytes")
    stub = _StubChatService(response=_response())

    result = _understand(
        stub,
        task="Compare the inputs.",
        inputs=(
            UnderstandInput(id="missing", kind=UnderstandInputKind.IMAGE, path=str(tmp_path / "missing.png")),
            UnderstandInput(id="design", kind=UnderstandInputKind.IMAGE, path=str(image)),
            UnderstandInput(id="empty", kind=UnderstandInputKind.TEXT, text="   "),
            UnderstandInput(id="badurl", kind=UnderstandInputKind.URL, url="ftp://example.com/file"),
            UnderstandInput(id="design", kind=UnderstandInputKind.TEXT, text="duplicate id"),
        ),
    )

    assert result.ok is True
    assert _outcomes(result) == [
        ("missing", "image", UnderstandingOutcome.SKIPPED),
        ("design", "image", UnderstandingOutcome.ANALYZED),
        ("empty", "text", UnderstandingOutcome.SKIPPED),
        ("badurl", "url", UnderstandingOutcome.SKIPPED),
        ("design", "text", UnderstandingOutcome.SKIPPED),
    ]
    assert [warning.code for warning in result.warnings] == ["input_skipped"] * 4
    assert all("was skipped" in warning.message for warning in result.warnings)
    assert stub.captured_request is not None
    assert stub.captured_request.files == (str(image),)


def test_understand_skips_duplicate_ids_so_one_id_keeps_one_identity():
    stub = _StubChatService(response=_response())

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(
            UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="first"),
            UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="second"),
        ),
    )

    assert result.ok is True
    outcomes = _outcomes(result)
    assert outcomes == [
        ("notes", "text", UnderstandingOutcome.ANALYZED),
        ("notes", "text", UnderstandingOutcome.SKIPPED),
    ]
    assert "duplicate input id" in result.data.inputs[1].detail


def test_understand_skips_blank_ids_and_unsupported_kinds():
    stub = _StubChatService(response=_response())

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(
            UnderstandInput(id="", kind=UnderstandInputKind.TEXT, text="no id"),
            UnderstandInput(id="weird", kind="snippet", text="unsupported kind"),
            UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="usable"),
        ),
    )

    assert result.ok is True
    outcomes = _outcomes(result)
    assert outcomes[0] == ("", "text", UnderstandingOutcome.SKIPPED)
    assert outcomes[1] == ("weird", "snippet", UnderstandingOutcome.SKIPPED)
    assert outcomes[2] == ("notes", "text", UnderstandingOutcome.ANALYZED)


def test_understand_bounds_the_typed_input_list():
    stub = _StubChatService(response=_response())

    result = _understand(
        stub,
        task="Analyze.",
        inputs=tuple(
            UnderstandInput(id=f"note-{index}", kind=UnderstandInputKind.TEXT, text=f"note {index}")
            for index in range(17)
        ),
    )

    assert result.ok is True
    assert result.data is not None
    analyzed = [item for item in result.data.inputs if item.outcome is UnderstandingOutcome.ANALYZED]
    skipped = [item for item in result.data.inputs if item.outcome is UnderstandingOutcome.SKIPPED]
    assert len(analyzed) == 16
    assert [item.id for item in skipped] == ["note-16"]
    assert "input limit exceeded" in skipped[0].detail


def test_understand_rejects_image_paths_that_are_not_usable_images(tmp_path):
    not_an_image = tmp_path / "notes.txt"
    not_an_image.write_text("plain text")
    stub = _StubChatService(response=_response())

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(UnderstandInput(id="bad", kind=UnderstandInputKind.IMAGE, path=str(not_an_image)),),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.INVALID_ARGUMENT
    assert result.data is not None
    assert result.data.inputs[0].outcome is UnderstandingOutcome.SKIPPED
    assert "local image rejected" in result.data.inputs[0].detail
    assert stub.captured_request is None


# ---------------------------------------------------------------------------
# Typed rejection and failure classification
# ---------------------------------------------------------------------------


def test_understand_rejects_blank_task_and_empty_inputs_before_client_use():
    stub = _StubChatService(response=_response())

    blank_task = _understand(stub, task="   ", inputs=(UnderstandInput(id="a", kind=UnderstandInputKind.TEXT, text="x"),))
    empty_inputs = _understand(stub, task="Analyze.", inputs=())

    for result in (blank_task, empty_inputs):
        assert result.ok is False
        assert result.error is not None
        assert result.error.code is DomainErrorCode.INVALID_ARGUMENT
        assert result.data is None
        assert result.meta.verification_status == "input_rejected"
    assert stub.captured_request is None


def test_understand_image_rejects_blank_image_before_client_use():
    stub = _StubChatService(response=_response())

    result = asyncio.run(UnderstandService(stub).understand_image(UnderstandImageRequest(image="   ")))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.INVALID_ARGUMENT
    assert stub.captured_request is None


def test_understand_marks_accepted_inputs_failed_when_the_chat_errors():
    stub = _StubChatService(error=RuntimeError("upstream exploded"))

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="some notes"),),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.INTERNAL_ERROR
    assert result.data is not None
    assert result.data.analysis == ""
    assert _outcomes(result) == [("notes", "text", UnderstandingOutcome.FAILED)]
    assert result.meta.verification_status == "exception_classified"
    assert result.meta.details["failed"] == 1


def test_understand_preserves_chat_failure_errors_and_marks_inputs_failed():
    chat_failure = DomainResult.failure(
        DomainErrorCode.NETWORK_ERROR,
        "The upstream service could not be reached.",
        retryable=True,
        suggested_action="Check network or proxy settings and retry.",
    )
    stub = _StubChatService(failure=chat_failure)

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="some notes"),),
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is DomainErrorCode.NETWORK_ERROR
    assert result.error.retryable is True
    assert result.data is not None
    assert _outcomes(result) == [("notes", "text", UnderstandingOutcome.FAILED)]


def test_understand_reports_backend_and_lifecycle_evidence():
    stub = _StubChatService(response=_response(text="evidence-backed analysis"))

    result = _understand(
        stub,
        task="Analyze.",
        inputs=(UnderstandInput(id="notes", kind=UnderstandInputKind.TEXT, text="some notes"),),
    )

    assert result.ok is True
    data = result.data
    assert data is not None
    assert data.requested_model == "flash"
    assert data.effective_model == "gemini-3-flash"
    assert data.observed_backend == "gemini-3-flash"
    # Lifecycle evidence is owned by the shared ChatService and surfaced verbatim.
    assert data.lifecycle is not None
    assert result.meta.requested_backend == "flash"
    assert result.meta.effective_backend == "gemini-3-flash"


def test_understanding_input_artifact_identity_is_stable_and_location_backed(tmp_path):
    image = tmp_path / "design.png"
    image.write_bytes(b"fake image bytes")
    stub = _StubChatService(response=_response())

    first = _understand(
        stub,
        task="Describe.",
        inputs=(UnderstandInput(id="design", kind=UnderstandInputKind.IMAGE, path=str(image)),),
    )
    second = _understand(
        stub,
        task="Describe again.",
        inputs=(UnderstandInput(id="design", kind=UnderstandInputKind.IMAGE, path=str(image)),),
    )

    assert first.data is not None and second.data is not None
    first_artifact = first.data.inputs[0].artifact
    second_artifact = second.data.inputs[0].artifact
    assert first_artifact is not None and second_artifact is not None
    assert first_artifact.id == second_artifact.id
    assert first_artifact.id.startswith("artifact_")
    assert first_artifact.local_path == str(image)
    assert first_artifact.state.value == "local"
