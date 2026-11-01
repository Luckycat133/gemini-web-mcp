"""Explicit, restart-safe recovery of operations started by Gemini tools."""

import logging

from ..adapters import domain_error_boundary, domain_failure_text, domain_text, append_artifact_block
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client, finalize_remote_chat_cleanup
from ..domain import DomainResult
from ..domain.operations import OperationReadData
from ..infrastructure.operation_store import OperationStore
from ..services.operations import OperationService
from .annotations import READS_PRIVATE_REMOTE, DESTRUCTIVE_REMOTE

logger = logging.getLogger(__name__)


def _service() -> OperationService:
    return OperationService(OperationStore(), get_gemini_client, initialize_client, finalize_remote_chat_cleanup)


def _present(result: DomainResult[OperationReadData]) -> list[TextContent]:
    data = result.data
    if data is None:
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    task_operation = data.operation in {"gemini_run_task", "gemini_notebook_chat"}
    if not result.ok and not task_operation:
        return domain_text(result, domain_failure_text(result), use_result_data=True)
    state = getattr(data.state, "value", data.state)
    text = f"Operation {data.operation_id}: {state}."
    if not result.ok:
        text = domain_failure_text(result) + "\n" + text
    if task_operation:
        answer = data.task_response or data.answer
        if data.setup_required:
            app = f" ({data.setup_app_name})" if data.setup_app_name else ""
            text += f"\nThe Task needs native app connection setup{app}; it is waiting for attention."
            text += "\nConnect or decline in the original Gemini Task. gemini_run_task text replies cannot connect the app or complete OAuth."
            if data.setup_url:
                text += "\nOpen the Task: " + data.setup_url
        elif data.needs_attention:
            text += "\nA native app connection card was previously observed. Open the original Task to check its current setup state."
            text += "\nThis metadata-only read does not revalidate the card. Text replies cannot connect the app or complete OAuth."
            if data.setup_url:
                text += "\nOpen the Task: " + data.setup_url
        elif data.input_required:
            text += "\nThe Task requires explicit input; its chat remains protected from cleanup."
            if data.input_prompt:
                text += "\n\n" + data.input_prompt
            for option in data.input_options:
                text += f"\n{option['label']}: {option['prompt']}"
            if data.input_url:
                text += "\nOpen the Task: " + data.input_url
        elif answer:
            text += "\n\n" + answer
        elif data.upstream_status is not None:
            text += f"\nObserved Task status: {data.upstream_status}."
        else:
            text += "\nTask progress is unverified; no generation was repeated."
    elif data.report_available:
        report = getattr(data, "report", None)
        text += "\n\n" + (report or "The research report is ready; call gemini_get_operation_result to retrieve it.")
    elif data.media_type:
        if data.artifact_state is not None:
            text += f"\nArtifact state: {data.artifact_state.value}."
        elif data.worker_active:
            text += "\nA background worker is observing the original generation."
        else:
            text += "\nNo verified artifact is available; the generation will not be submitted again."
    else:
        text += "\nThis read did not return a verified research report; upstream progress is unconfirmed."
    cleanup = getattr(data, "cleanup", None)
    if cleanup is not None and getattr(cleanup.state, "value", cleanup.state) in {"failed", "pending", "invalid_id"}:
        text += "\nAutomatic chat cleanup is not verified; inspect the cleanup state before claiming deletion."
    return append_artifact_block(domain_text(result, text, use_result_data=True), data.artifacts)


def register_operation_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    @domain_error_boundary("gemini_get_operation_status", logger)
    async def gemini_get_operation_status(operation_id: str) -> list[TextContent]:
        """Check one saved operation by its explicit opaque handle without starting new work.

        Works across server restarts using the local operation database. Reads
        only the remote chat recorded for this handle; it never lists account
        history or repeats a generation or research start. A missing result is unconfirmed
        progress, not evidence that the provider is still running.
        """
        return _present(await _service().get_status(operation_id))

    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_get_operation_result", logger)
    async def gemini_get_operation_result(operation_id: str) -> list[TextContent]:
        """Recover verified media, a research document or a Task answer for one saved handle.

        Media results return stable local artifacts, independently reverified from
        persisted locators after a restart; missing or altered files fail verification.
        Interrupted media observation resumes read-only and never repeats generation.
        Research requires a ready nonempty document saved and verified as a full
        local Artifact before default cleanup. Result reads return a bounded
        preview plus the complete file, and rehash cached reports after restart
        without new generation or remote authentication. The metadata database
        stores locators and digests, never report text. Missing or altered files
        remain partial. Report derivatives accept this same operation_id.
        Unknown or expired handles fail before
        remote access; this call never starts or restarts research.
        Task answers are read from the exact saved turn and preserved in a verified
        local artifact before cleanup; the inline preview is bounded. A cached Task
        artifact remains usable after its remote thread is deleted.
        """
        return _present(await _service().get_result(operation_id))
