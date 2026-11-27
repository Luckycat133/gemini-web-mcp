"""Thin function adapter for asynchronous Gemini Task execution."""

import logging
from typing import Annotated, Literal

from pydantic import Field

from ..adapters import Context, domain_error_boundary, domain_failure_text, domain_text, make_progress_callback
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..services.background import submit_task
from ..services.operations import MAX_RESULT_WAIT_SECONDS
from .annotations import DESTRUCTIVE_REMOTE

logger = logging.getLogger(__name__)

_MODEL = Literal["hydrogen", "neon", "carbon", "auto"]
_THINKING = Literal["low", "medium", "high"]


def register_lean_task_tool(mcp: MCPServer) -> None:
    @mcp.tool(name="run-task", annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("run-task", logger)
    async def run_task(
        prompt: str,
        model: _MODEL = "hydrogen",
        thinking_level: _THINKING = "medium",
        notebook_ids: list[str] | None = None,
        operation_id: str | None = None,
        input_id: str | None = None,
        input_option_id: str | None = None,
        wait_seconds: Annotated[int, Field(ge=0, le=MAX_RESULT_WAIT_SECONDS)] = 240,
        timeout_seconds: int = 600,
        retain_chat: bool = False,
        ctx: Context | None = None,
    ) -> list[TextContent]:
        """Start or reply to a Gemini Task and wait for completion with progress updates.

        Use `ask-gemini` for Q&A or multimodal file/URL analysis; use `run-task` for
        multi-step background browser/app tasks. Waits up to 240s by default (`wait_seconds=0`
        returns a handle immediately) and resumes via `get-operation`. For `input_required`,
        resend with original `operation_id`, `input_id`, `input_option_id`, and prompt.
        """
        result = await submit_task({
            "prompt": prompt, "model": model, "thinking_level": thinking_level,
            "notebook_ids": notebook_ids, "timeout_seconds": timeout_seconds,
            "retain_chat": retain_chat, "delete_after_seconds": None,
            "input_id": input_id, "input_option_id": input_option_id,
        }, operation_id=operation_id)
        handle = result.data.operation_id if result.data is not None else None
        if result.ok and handle and wait_seconds > 0:
            from .operations import _present, _service

            service = _service()
            store = getattr(service, "store", None)
            if store is not None and store.get(handle) is not None:
                progress_cb = make_progress_callback(ctx)
                waited = await (
                    service.get_result(handle, wait_seconds=wait_seconds, on_progress=progress_cb)
                    if progress_cb is not None
                    else service.get_result(handle, wait_seconds=wait_seconds)
                )
                return _present(waited, include_text=True)
        text = f"Task accepted. Operation handle: {handle}" if result.ok else domain_failure_text(result)
        return domain_text(result, text, use_result_data=True)


def register_task_generation_tools(mcp: MCPServer) -> None:
    @mcp.tool(annotations=DESTRUCTIVE_REMOTE)
    @domain_error_boundary("gemini_run_task", logger)
    async def gemini_run_task(
        prompt: str, model: str = "flash", thinking_level: str = "standard",
        notebook_ids: list[str] | None = None, timeout_seconds: int = 600,
        retain_chat: bool = False, delete_after_seconds: int | None = None,
        operation_id: str | None = None,
        input_id: str | None = None,
        input_option_id: str | None = None,
    ) -> list[TextContent]:
        """Start a background Task for explicit Task, ongoing work, progress or browser operation.

        Default to gemini_ask for Chat, writing, Notebook analysis and supported app
        requests. Use exact notebook_ids from `gemini-web-cli notebooks list`.
        Discovered Web models: Hydrogen (flash default), Neon (pro), and Carbon.
        Thinking accepts low/medium/high (standard/extended alias low/high). Manage
        saved browser permissions with `gemini-web-cli preferences`.
        Use gemini_get_operation_status/result on the original handle. The worker
        verifies the answer and complete native file inventory before owned cleanup.
        retain_chat=true or an associated schedule protects the thread. Verify app
        changes independently. Include only explicitly authorized app mutations.
        For input_required, send the original operation_id and input_id plus the exact
        input_option_id and prompt from input_options, with the user's authorization.
        Repeating that input_id never resends. Model and retention carry forward.
        Unsupported, hidden/disabled or subagent cards require the native UI.
        """
        result = await submit_task({
            "prompt": prompt, "model": model, "thinking_level": thinking_level,
            "notebook_ids": notebook_ids, "timeout_seconds": timeout_seconds,
            "retain_chat": retain_chat, "delete_after_seconds": delete_after_seconds,
            "input_id": input_id,
            "input_option_id": input_option_id,
        }, operation_id=operation_id)
        handle = result.data.operation_id if result.data is not None else None
        text = f"Task accepted. Operation handle: {handle}" if result.ok else domain_failure_text(result)
        return domain_text(result, text, use_result_data=True)
