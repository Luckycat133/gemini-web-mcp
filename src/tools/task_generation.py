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
        """Start a Gemini Task in the background and return a persistent operation handle.

        Use when the user requests Task or delegates a workflow needing ongoing
        execution, work progress or browser operation. Default to gemini_ask for
        answers, writing, Notebook analysis and direct app requests supported in
        Chat; complexity, apps and background execution alone do not require Task.
        Name apps naturally and check their mode-specific availability if needed.
        notebook_ids selects exact source notebooks from the account surface.
        Task models are Flash (default) or Argon. thinking_level accepts low,
        medium or high; standard/extended remain aliases for low/high. Native
        browser/permission preferences are managed by account gemini_preferences,
        independently of this call.
        Poll the same handle with gemini_get_operation_status/result; never
        resubmit to check progress. The worker verifies the full answer and
        complete recognized native file inventory before cleaning the owned
        thread unless retention or an associated schedule
        protects it. retain_chat=true requests retention.
        Task completion does not independently prove an external app mutation.
        A Task may perform the app changes explicitly requested in its prompt.
        When result returns input_required, use its input_id and the SAME
        operation_id with the exact prompt and input_option_id from input_options
        to answer it once. Native action choices are sent as their exact identities;
        button text alone does not select a permission scope or browser choice.
        Unsupported, hidden/disabled or subagent cards require the native UI.
        Do not approve a card without the user's authorization. Repeating the
        same input_id never resends it; original model and retention are kept.
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
