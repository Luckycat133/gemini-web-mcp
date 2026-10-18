"""Verified native Canvas plain-document replacement without generation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from typing import Any

from ..domain import DomainErrorCode, DomainResult, DomainWarning, OperationState, result_from_exception
from ..infrastructure.canvas_contracts import (
    CanvasDocumentShapeError, UnsupportedCanvasDocument, plain_document_matches, replace_plain_document,
)
from ..infrastructure.rpc_contracts import execute_contract, get_contract
from ..infrastructure.rpc_parsers import parse_rpc_envelope
from .account import account_contract_result

logger = logging.getLogger(__name__)


def _failure_with_identity(
    result: DomainResult[dict[str, Any]], artifact_id: str, *, submitted: bool,
) -> DomainResult[dict[str, Any]]:
    assert result.error is not None
    return replace(result, data={"artifact_id": artifact_id, "submitted": submitted, "updated": False},
                   error=replace(result.error, retryable=False,
                                 suggested_action="Read the same Canvas artifact_id to recover; do not automatically replay this edit."))


async def _read_exact(client: Any, artifact_id: str) -> tuple[DomainResult[dict[str, Any]], Any]:
    response = await execute_contract(client, "canvas.get", artifact_id=artifact_id)
    observed = account_contract_result(response, "canvas.get", expected_id=artifact_id)
    if not observed.ok:
        return observed, None
    value = (observed.data or {}).get("value")
    if value is None:
        return DomainResult.failure(DomainErrorCode.ARTIFACT_NOT_RETURNED,
                                    "The exact Canvas artifact was not returned.", details=observed.meta.details), None
    # account_contract_result has already required a unique matching envelope.
    envelope = parse_rpc_envelope(str(getattr(response, "text", "") or ""), get_contract("canvas.get").rpc_id)
    return observed, envelope.bodies[0]


async def replace_canvas_text_result(
    client: Any, artifact_id: str, *, text: str,
) -> DomainResult[dict[str, Any]]:
    """Replace a document's entire text and rich formatting, then read it afresh.

    This updates the selected artifact. Completion requires the same identity
    and all four native content slots, including equivalent provider-normalized
    paragraphs; an RPC acknowledgement alone is partial.
    """
    if (not isinstance(artifact_id, str) or not artifact_id.strip() or artifact_id != artifact_id.strip()
            or not isinstance(text, str)):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT,
                                    "An exact Canvas artifact_id and string replacement text are required.")
    submitted = False
    stage = "read_before"
    try:
        before, raw = await _read_exact(client, artifact_id)
        if not before.ok:
            return _failure_with_identity(before, artifact_id, submitted=False)
        try:
            updated = replace_plain_document(raw, text)
        except UnsupportedCanvasDocument as error:
            return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, str(error),
                                        data={"artifact_id": artifact_id, "submitted": False, "updated": False})
        except CanvasDocumentShapeError as error:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, str(error),
                                        data={"artifact_id": artifact_id, "submitted": False, "updated": False})
        stage = "update"
        submitted = True
        response = await execute_contract(client, "canvas.update", artifact_id=artifact_id, updated_ez=updated)
        ack = account_contract_result(response, "canvas.update")
        if not ack.ok:
            return _failure_with_identity(ack, artifact_id, submitted=True)
        stage = "read_after"
        after, actual = await _read_exact(client, artifact_id)
        matches = False
        if after.ok:
            try:
                matches = plain_document_matches(actual, text)
            except (CanvasDocumentShapeError, UnsupportedCanvasDocument):
                matches = False
        status = "verified_updated" if matches else "read_back_mismatch" if after.ok else "read_back_unavailable"
        return DomainResult.success(
            {"artifact_id": artifact_id, "submitted": True, "accepted": True, "updated": matches,
             "formatting_replaced": True,
             "item": (after.data or {}).get("value") if after.ok else None},
            operation_state=OperationState.COMPLETED if matches else OperationState.ACCEPTED,
            verification_status=status,
            warnings=() if matches else (DomainWarning(
                "MUTATION_UNVERIFIED", "Canvas update was acknowledged but all four content slots were not verified.",
                "Read the same Canvas artifact_id; do not automatically replay the edit.",
            ),),
            details={"mutation": dict(ack.meta.details), "verification": dict(after.meta.details)},
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        failed = result_from_exception(error, logger=logger, operation="canvas.replace_text")
        failed = replace(failed, meta=replace(failed.meta, details={**failed.meta.details, "stage": stage}))
        return _failure_with_identity(failed, artifact_id, submitted=submitted)
