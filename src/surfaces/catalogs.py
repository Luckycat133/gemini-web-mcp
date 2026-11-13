"""The two public business catalogs; legacy adapters remain separately available."""

FUNCTION_TOOLS = frozenset({
    "gemini_ask", "gemini_understand_image", "gemini_understand", "gemini_research",
    "gemini_start_chat", "gemini_send_message", "gemini_list_sessions", "gemini_reset_session",
    "gemini_generate_image", "gemini_edit_image", "gemini_generate_video", "gemini_generate_music",
    "gemini_get_operation_status", "gemini_get_operation_result",
    "gemini_list_research_report_actions", "gemini_create_from_research_report",
    "gemini_run_task",
})
ACCOUNT_TOOLS = frozenset({
    "gemini_history", "gemini_delete_chat", "gemini_notebooks", "gemini_move_chat_to_notebook",
    "gemini_account_inventory", "gemini_list_scheduled_actions", "gemini_get_scheduled_action",
    "gemini_create_scheduled_action", "gemini_delete_scheduled_action", "gemini_manage_gems", "gemini_cleanup",
    "gemini_manage_chat",
    "gemini_tasks", "gemini_task_browser", "gemini_preferences", "gemini_skills",
    "gemini_manage_notebook",
})
