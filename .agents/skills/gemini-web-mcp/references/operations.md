# Long Operations

Load this reference for Deep Research, video, music, and future asynchronous generation.

## Current Tools

- `gemini_research` starts Deep Research asynchronously and returns `operation_id`, `upstream_operation_id`, and `upstream_chat_id` when observed. The local `operation_id` is a correlation ID only: no current tool accepts it for status, result, or cancellation.
- Primary `gemini_deep_research(wait_for_completion=false, retain_chat=true)` starts without waiting and returns observed upstream IDs. It does not create a durable local handle.
- Compact `create(type="music")` and primary music tools may wait for a response or return `queued`. They do not have a separate status/result/cancel tool. Generic video tools remain registered for compatibility, but the current live check did not produce a video Artifact; use Gemini's dedicated Videos page.

For Research, retain the chat and use the observed `upstream_chat_id` to inspect it later through compact `history(action="read", chat_id=...)` or primary `gemini_history(action="read", chat_id=...)` in the `history` profile. These reads can truncate long turns or show only a completion notice. When the report text is absent, primary `gemini_create_from_research_report(chat_id=..., artifact_type="webpage")` in the `core` profile can attempt to extract a local report webpage. Check the resulting Artifact state and content; an unreadable or unfinished chat is inconclusive. If no chat ID was observed, report that recovery is unavailable through the current surface. Do not start the same query again automatically.

There is no `operation(...)`, `gemini_get_operation_status`, `gemini_get_operation_result`, or `gemini_cancel_operation` tool in the current release.

## During a Long Call

Preserve every observed upstream identifier and Artifact identity. `timed_out` describes the local wait, not proof that Gemini stopped. A `queued` response is not a completed Artifact. Do not automatically repeat a generation request after a timeout, because it may create a duplicate.

Media deadlines include generation, recovery reads, verification, and save. Research polling/report retrieval consumes the remaining phase budget. Changed chat text, progress notices, and quota refusals do not establish report completion: inspect completed-state/report evidence and preserve failed, cancelled, or unavailable results. A completion notice without retrievable report content still requires recovery.

The planned SQLite OperationService will add restart-safe `status`, `result`, and `cancel` by explicit operation ID. Those tools and durable handles are not available in the current release.
