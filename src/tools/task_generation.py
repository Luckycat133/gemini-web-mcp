"""Thin function adapter for asynchronous Gemini Task execution."""

import logging

from ..adapters import domain_error_boundary, domain_failure_text, domain_text
from ..adapters.mcp_sdk import MCPServer, TextContent
from ..services.background import submit_task
from .annotations import DESTRUCTIVE_REMOTE

logger = logging.getLogger(__name__)


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
        requests. Use exact notebook_ids from gemini_notebooks. Flash is default and
        Argon is supported. Thinking accepts low/medium/high (standard/extended alias
        low/high). Manage saved browser permissions with gemini_preferences.
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
