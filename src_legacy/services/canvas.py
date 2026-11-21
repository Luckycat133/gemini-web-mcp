"""Read observed Canvas artifact versions through the native artifact API."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..client_wrapper import schedule_remote_chat_cleanup
from ..constants import normalize_model_alias, resolve_model_name
from ..domain import (
    ArtifactKind, CleanupObservation, CleanupState, ConversationLifecycleMetadata,
    DomainErrorCode, DomainResult, DomainWarning, OperationState, is_valid_remote_chat_id, result_from_exception,
)
from ..infrastructure.rpc_contracts import execute_contract
from ..thinking_client import client_generation_once
from .account import account_contract_result
from .artifacts import artifact_from_local_path, response_chat_id

logger = logging.getLogger(__name__)


async def get_canvas_artifact_result(client: Any, artifact_id: str) -> DomainResult[dict[str, Any]]:
    """Read one exact version ID; an empty body does not imply usable content."""
    if not isinstance(artifact_id, str) or not artifact_id.strip() or artifact_id != artifact_id.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact Canvas artifact_id is required.")
    response = await execute_contract(client, "canvas.get", artifact_id=artifact_id)
    observed = account_contract_result(response, "canvas.get", expected_id=artifact_id)
    if not observed.ok or observed.data is None:
        return observed
    item = observed.data["value"]
    if item is None:
        return DomainResult.failure(DomainErrorCode.ARTIFACT_NOT_RETURNED, "The requested artifact version was not returned.",
                                    verification_status="artifact_not_observed", details=observed.meta.details)
    return DomainResult.success(item, verification_status="observed", details=observed.meta.details)


async def list_canvas_chat_artifacts_result(
    client: Any, chat_id: str, *, limit: int = 10,
) -> DomainResult[dict[str, Any]]:
    """Read genuine version IDs from an existing source, without resubmitting generation."""
    if (not isinstance(chat_id, str) or not chat_id.strip() or chat_id != chat_id.strip()
            or type(limit) is not int or not 1 <= limit <= 100):
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact source chat_id and a limit between 1 and 100 are required.")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_tokens: set[str] = set()
    token = None
    for page in range(20):
        response = await execute_contract(client, "canvas.chat_artifacts", chat_id=chat_id,
                                          limit=limit, next_page_token=token)
        observed = account_contract_result(response, "canvas.chat_artifacts", expected_chat_id=chat_id)
        if not observed.ok or observed.data is None:
            return observed
        value = observed.data["value"]
        for item in value["items"]:
            if item["artifact_id"] not in seen_ids:
                items.append(item)
                seen_ids.add(item["artifact_id"])
        token = value["next_page_token"]
        if not token or len(items) >= limit:
            complete = not token and len(items) <= limit
            return DomainResult.success(
                {"chat_id": chat_id, "items": items[:limit], "limit": limit,
                 "complete": complete, "next_page_token": token, "pages": page + 1},
                operation_state=OperationState.COMPLETED if complete else OperationState.PARTIAL,
                verification_status="observed" if complete else "bounded_read",
            )
        if token in seen_tokens:
            return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "Canvas history pagination repeated a continuation.")
        seen_tokens.add(token)
    return DomainResult.success(
        {"chat_id": chat_id, "items": items[:limit], "limit": limit,
         "complete": False, "next_page_token": token, "pages": 20},
        operation_state=OperationState.PARTIAL, verification_status="bounded_read",
    )


async def list_canvas_versions_result(client: Any, artifact_id: str) -> DomainResult[dict[str, Any]]:
    if not isinstance(artifact_id, str) or not artifact_id.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "An exact Canvas artifact_id is required.")
    response = await execute_contract(client, "canvas.versions", artifact_id=artifact_id)
    observed = account_contract_result(response, "canvas.versions")
    if not observed.ok or observed.data is None:
        return observed
    return DomainResult.success({"artifact_id": artifact_id, "items": observed.data["value"]},
                                verification_status="observed", details=observed.meta.details)


async def generate_canvas_result(
    client: Any, *, prompt: str, model: str = "flash", thinking_level: str = "standard",
) -> DomainResult[dict[str, Any]]:
    """Create native Canvas content, then read its actual artifacts.

    A prompt response alone cannot prove Canvas output. The source is retained
    for chat_artifacts/get recovery, including after a provider timeout.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "A nonempty Canvas prompt is required.")
    resolved = resolve_model_name(normalize_model_alias(model))
    if not callable(getattr(client, "start_owned_chat", None)):
        return DomainResult.failure(DomainErrorCode.CAPABILITY_UNAVAILABLE, "This client cannot track native Canvas creation.")
    source_id: str | None = None
    cleanup = CleanupObservation(source="gemini_canvas")

    def retain_source(cid: Any) -> None:
        nonlocal source_id, cleanup
        if is_valid_remote_chat_id(cid):
            source_id = cid
            cleanup = schedule_remote_chat_cleanup(
                cid, retain_chat=True, delete_after_seconds=None, source="gemini_canvas",
            )

    def source_data() -> dict[str, Any]:
        return {
            "chat_id": source_id, "source_chat_id": source_id,
            "retained": cleanup.state is CleanupState.RETAINED, "cleanup": cleanup,
            "lifecycle": ConversationLifecycleMetadata.stateless(
                upstream_chat_id=source_id, retain_chat=True, cleanup=cleanup,
            ),
        }

    chat = client.start_owned_chat(model=resolved, on_chat_observed=retain_source)
    try:
        with client_generation_once(client):
            response = await client.generate_content(prompt=prompt, model=resolved, thinking_level=thinking_level,
                                                     tool_mode="canvas", chat=chat)
        retain_source(source_id or getattr(chat, "observed_chat_id", None) or response_chat_id(response) or getattr(chat, "cid", None))
    except asyncio.CancelledError:
        retain_source(source_id or getattr(chat, "observed_chat_id", None) or getattr(chat, "cid", None))
        raise
    except Exception as error:
        retain_source(source_id or getattr(chat, "observed_chat_id", None) or getattr(chat, "cid", None))
        failed = result_from_exception(error, logger=logger, operation="canvas.generate")
        assert failed.error is not None
        data = source_data()
        action = ("Use canvas chat_artifacts with the observed chat_id; do not repeat creation." if source_id
                  else "Inspect history metadata for a possible Canvas source; do not automatically repeat creation.")
        warning = ("CANVAS_SOURCE_RETAINED" if data["retained"] else
                   "CANVAS_RETENTION_UNVERIFIED" if source_id else "CANVAS_SOURCE_UNOBSERVED")
        return replace(failed, data=data,
                       error=replace(failed.error, retryable=False,
                                     suggested_action=action),
                       warnings=(DomainWarning(warning, "Canvas submission did not finish; inspect its source and cleanup evidence.", action),),
                       meta=replace(failed.meta, details={**failed.meta.details,
                                    "lifecycle": data["lifecycle"], "cleanup": cleanup}))
    if not source_id:
        return DomainResult.failure(DomainErrorCode.UPSTREAM_CHANGED, "No Canvas source identity was observed.",
                                    data=source_data(),
                                    verification_status="source_unobserved")
    data = {**source_data(), "items": []}
    details = {"lifecycle": data["lifecycle"], "cleanup": cleanup}
    warnings = () if data["retained"] else (DomainWarning(
        "CANVAS_RETENTION_UNVERIFIED", "The observed Canvas source could not be registered as retained.",
        "Inspect the returned cleanup state before relying on source recovery.",
    ),)
    try:
        observed = await list_canvas_chat_artifacts_result(client, source_id)
    except Exception:
        observed = None
    if observed is not None and observed.ok and observed.data is not None:
        data["items"] = [item for item in observed.data["items"] if item.get("kind") in {"document", "code"}
                         and item.get("text")]
        if data["items"]:
            return DomainResult.success(data, verification_status="native_canvas_observed", warnings=warnings, details=details,
                                        operation_state=OperationState.COMPLETED if data["retained"] else OperationState.PARTIAL)
    return DomainResult.success(
        data, operation_state=OperationState.ACCEPTED, verification_status="canvas_not_observed", details=details,
        warnings=(*warnings, DomainWarning("CANVAS_NOT_OBSERVED", "No new native Canvas artifact was read back. Inspect source recovery and cleanup evidence.")),
    )


async def export_canvas_result(
    client: Any, *, artifact_id: str, output_path: str,
) -> DomainResult[dict[str, Any]]:
    """Export exactly the observed version as UTF-8 without overwriting files."""
    path = Path(output_path).expanduser()
    if not path.is_absolute() or not path.name or path.exists():
        return DomainResult.failure(DomainErrorCode.INVALID_ARGUMENT, "Use a new absolute output_path.")
    observed = await get_canvas_artifact_result(client, artifact_id)
    if not observed.ok or observed.data is None:
        return observed
    text = observed.data.get("text")
    if not isinstance(text, str) or not text:
        return DomainResult.failure(DomainErrorCode.ARTIFACT_NOT_RETURNED, "The observed Canvas version has no exportable text.")
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
        if path.read_text(encoding="utf-8") != text:
            return DomainResult.failure(DomainErrorCode.VERIFICATION_FAILED, "The Canvas export could not be verified.")
        artifact = artifact_from_local_path(ArtifactKind.FILE, str(path), title=observed.data.get("title"))
        return DomainResult.success({"item": observed.data, "artifacts": [artifact]}, verification_status="verified_exported")
    except OSError as error:
        return result_from_exception(error, logger=logger, operation="canvas.export")
