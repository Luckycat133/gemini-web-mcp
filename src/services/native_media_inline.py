"""Wait for one protected native media job through its original operation handle."""

from __future__ import annotations

import asyncio
import logging
from math import ceil
from typing import Any

from ..domain import ArtifactState, DomainResult, InlineMediaArtifactData, OperationState, result_from_exception
from ..domain.operations import LongOperationData, OperationReadData
from ..infrastructure.operation_store import OperationStore
from .deadlines import await_before_deadline
from .operations import MAX_RESULT_WAIT_SECONDS, OperationService

logger = logging.getLogger(__name__)


InlineMediaData = InlineMediaArtifactData | LongOperationData | OperationReadData


def _with_data(result: DomainResult[Any], data: InlineMediaData | None) -> DomainResult[InlineMediaData]:
    return DomainResult(ok=result.ok, data=data, error=result.error, warnings=result.warnings, meta=result.meta)


async def submit_and_wait_native_media(
    request: dict[str, Any], *, operation: str, timeout_seconds: int,
) -> DomainResult[InlineMediaData]:
    from ..client_wrapper import get_gemini_client, initialize_client
    from .background import submit_media

    submission = await submit_media({
        **request,
        "output_dir": request.get("output_dir") or "generated_media",
        "web_media_transport": "native",
    }, operation=operation)
    accepted = submission.data
    if not submission.ok or accepted is None or accepted.operation_id is None:
        return _with_data(submission, accepted)

    handle = accepted.operation_id
    clock = asyncio.get_running_loop().time
    deadline = clock() + timeout_seconds

    def elapsed() -> DomainResult[InlineMediaData]:
        data = OperationReadData(handle, operation, OperationState.PARTIAL, accepted.continuation_possible,
                                 False, "wait_elapsed", media_type=accepted.media_type)
        return DomainResult.success(data, operation_state=data.state, verification_status=data.verification_status)

    try:
        # The worker owns cleanup. Inline polling only reads or recovers the accepted operation.
        service = OperationService(OperationStore(), get_gemini_client, initialize_client)
        while (remaining := deadline - clock()) > 0:
            result = await await_before_deadline(
                service.get_result(handle, wait_seconds=min(MAX_RESULT_WAIT_SECONDS, ceil(remaining))),
                timeout=remaining,
            )
            data = result.data
            if data is None or data.verification_status != "wait_elapsed":
                break
        else:
            return elapsed()
    except TimeoutError:
        return elapsed()
    except Exception as error:
        result = result_from_exception(error, logger=logger, operation=operation)
        data = None

    if data is None:
        data = OperationReadData(handle, operation, result.meta.operation_state, accepted.continuation_possible,
                                 False, result.meta.verification_status, media_type=accepted.media_type)
    if result.ok and data.artifact_state == ArtifactState.LOCAL and data.artifacts:
        artifact = data.artifacts[0]
        return _with_data(result, InlineMediaArtifactData(
            state=ArtifactState.LOCAL, artifacts=data.artifacts, requested_model=artifact.requested_backend,
            request_model=artifact.request_model, effective_backend=artifact.effective_backend,
            observed_backend=artifact.observed_backend, source_chat_id=artifact.source_chat_id,
            media_type=data.media_type, operation_id=handle, cleanup=data.cleanup,
        ))
    return _with_data(result, data)
