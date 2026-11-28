"""Explicit, restart-safe recovery of operations started by Gemini tools."""

import logging
from typing import Annotated

from pydantic import Field

from ..adapters import Context, domain_error_boundary, domain_failure_text, domain_text, append_artifact_block, make_progress_callback
from ..adapters.artifacts import omit_saved_preview
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..client_wrapper import get_gemini_client, initialize_client, finalize_remote_chat_cleanup
from ..domain import DomainErrorCode, DomainResult
from ..domain.operations import OperationReadData
from ..infrastructure.operation_store import OperationStore
from ..services.operations import MAX_RESULT_WAIT_SECONDS, OperationService
from ..services.task_inputs import task_auto_approve_enabled
from .annotations import READS_PRIVATE_REMOTE, DESTRUCTIVE_REMOTE

logger = logging.getLogger(__name__)


def _service() -> OperationService:
    return OperationService(OperationStore(), get_gemini_client, initialize_client, finalize_remote_chat_cleanup)


def _present(result: DomainResult[OperationReadData], *, include_text: bool = True) -> list[TextContent]:
    result, preview_omitted = (result, False) if include_text else omit_saved_preview(result)
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
        if data.operation == "gemini_run_task" and task_auto_approve_enabled():
            text += "\n🚩 Background auto-approval is enabled for supported interactive confirmation cards."
        answer = data.task_response or data.answer
        if data.setup_required:
            app = f" ({data.setup_app_name})" if data.setup_app_name else ""
            text += f"\n🚩 The Task needs native app connection setup{app}; it is waiting for attention."
            text += "\nConnect or decline in the original Gemini Task. gemini_run_task text replies cannot connect the app or complete OAuth."
            if data.setup_url:
                text += "\nOpen the Task: " + data.setup_url
        elif data.needs_attention:
            text += "\n🚩 A native app connection card was previously observed. Open the original Task to check its current setup state."
            text += "\nThis metadata-only read does not revalidate the card. Text replies cannot connect the app or complete OAuth."
            if data.setup_url:
                text += "\nOpen the Task: " + data.setup_url
        elif data.input_required:
            text += "\n🚩 The Task requires input; its chat remains protected from cleanup."
            if data.input_prompt:
                text += "\n\n" + data.input_prompt
            for option in data.input_options:
                text += f"\n{option['label']}: {option['prompt']}"
            if data.input_url:
                text += "\nOpen the Task: " + data.input_url
        elif answer:
            text += "\n\n" + answer
        elif preview_omitted:
            text += "\nFull answer in verified local artifact."
        elif data.upstream_status is not None:
            text += f"\nObserved Task status: {data.upstream_status}."
        elif data.operation == "gemini_notebook_chat":
            if state in {"accepted", "queued", "running", "partial"}:
                text += "\nNotebook Chat progress is unverified."
        else:
            text += "\nTask progress is unverified; no generation was repeated."
    elif data.report_available:
        report = getattr(data, "report", None)
        if preview_omitted:
            text += "\nFull report in verified local artifact."
        else:
            text += "\n\n" + (report or "The research report is ready; call gemini_get_operation_result to retrieve it.")
    elif data.media_type:
        if data.artifact_state is not None:
            text += f"\nArtifact state: {data.artifact_state.value}."
        elif state == "partial" and data.error_code == "RATE_LIMITED":
            text += "\nRead-back is temporarily limited. Wait and use `get-operation` with this operation ID."
        elif data.worker_active:
            text += "\nA background worker is observing the original generation."
        else:
            text += "\nNo verified artifact is available; the generation will not be submitted again."
    else:
        text += "\nThis read did not return a verified research report; upstream progress is unconfirmed."
    cleanup = getattr(data, "cleanup", None)
    if cleanup is not None and getattr(cleanup.state, "value", cleanup.state) in {"failed", "pending", "invalid_id"}:
        text += "\nAutomatic cleanup is pending verification."
    return append_artifact_block(domain_text(result, text, use_result_data=True), data.artifacts)


def register_operation_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=READS_PRIVATE_REMOTE)
    @domain_error_boundary("gemini_get_operation_status", logger)
    async def gemini_get_operation_status(operation_id: str) -> list[TextContent]:
        """Read status for one saved operation_id across restarts.

        Reads only the resource recorded for that handle. Missing results mean
        unconfirmed progress. Polling never repeats generation or reads account history.
        """
        return _present(await _service().get_status(operation_id))

    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_get_operation_result", logger)
    async def gemini_get_operation_result(
        operation_id: str, include_text: bool = True,
        wait_seconds: Annotated[int, Field(ge=0, le=MAX_RESULT_WAIT_SECONDS)] = 0,
    ) -> list[TextContent]:
        """Recover verified media, research, Task or Notebook Chat for one saved operation_id.

        Resumes observation without repeating generation. Unknown or expired handles
        fail before remote access. Media files are reverified after restart.
        Nonempty research and exact-turn answers return a bounded preview plus the
        complete verified local artifact before unretained chat cleanup. Cached files
        are rehashed and remain usable after cleanup. Cached research reads need no
        remote authentication. Missing or altered files stay unverified. Research
        derivatives use this same operation_id. Set include_text=false to omit a
        preview when the full verified file is returned.
        wait_seconds waits on the original worker for up to 300 seconds without
        repeating generation. Zero returns the current result immediately.
        """
        if wait_seconds:
            return _present(await _service().get_result(operation_id, wait_seconds=wait_seconds), include_text=include_text)
        return _present(await _service().get_result(operation_id), include_text=include_text)


def register_lean_operation_tool(mcp: MCPServer) -> None:
    @mcp.tool(name="get-operation", annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("get-operation", logger)
    async def get_operation(
        operation_id: str,
        wait_seconds: Annotated[int, Field(ge=0, le=MAX_RESULT_WAIT_SECONDS)] = 0,
        status_only: bool = False,
        include_text: bool = True,
        ctx: Context | None = None,
    ) -> list[TextContent]:
        """Check status or recover verified outputs for a saved operation_id.

        Never repeats generation. Use status_only=true for metadata status, or pass
        wait_seconds (0-300) to wait for and download verified media/research/task artifacts.
        """
        if status_only and wait_seconds > 0:
            invalid: DomainResult[OperationReadData] = DomainResult.failure(
                DomainErrorCode.INVALID_ARGUMENT,
                "status_only=true cannot be combined with wait_seconds > 0.",
            )
            return _present(invalid)
        if status_only:
            return _present(await _service().get_status(operation_id))
        if wait_seconds:
            progress_cb = make_progress_callback(ctx)
            service = _service()
            result = await (
                service.get_result(operation_id, wait_seconds=wait_seconds, on_progress=progress_cb)
                if progress_cb is not None
                else service.get_result(operation_id, wait_seconds=wait_seconds)
            )
            return _present(result, include_text=include_text)
        return _present(await _service().get_result(operation_id), include_text=include_text)
