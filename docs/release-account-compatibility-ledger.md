# v0.2.2 account compatibility ledger

Released [facades](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/surfaces/account.py) and [request models](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py), against [the candidate Account registration](https://github.com/Luckycat133/gemini-web-mcp/blob/2c4bb80fdba9a27cab13c1b4564fbb40aa8b2fa9/src/surfaces/account.py). This is a static migration inventory; no account, cookies or live service was used.

## Facade decisions

| Released request entry | Candidate counterparts and remaining gate |
| --- | --- |
| `gemini_history(request: HistoryRequest)` | Same name, different request shape; candidate deletes through a separate tool and manages pin/title separately. Compare metadata, content read/export, search and task-status semantics before migration. |
| `gemini_notebooks(request: NotebooksRequest)` | Same name, different request shape, and defaults to NotebookLM rather than released native Gemini Web Notebook semantics. Use explicit backend selection in future mapping; prove read/write parity per action. |
| `gemini_scheduled(request: ScheduledRequest)` | Split list/get/create/delete declarations. Released backend-specific triggers, update/pause/resume, and task schedule separation need explicit action-level checks. |
| `gemini_gems(request: GemsRequest)` | Candidate gemini_manage_gems is a differently shaped entry. Confirm action mapping, exact ID, and verification behavior. |
| `gemini_prompts(request: PromptsRequest)` | No matching candidate Account catalog entry; separate compatibility profiles are not evidence of Account facade parity. |
| `gemini_account(request: AccountRequest)` | Inventory/preferences/managed skills are separate declarations. Nested Canvas, Library, public sharing and Skills ZIP remain not demonstrated as equivalent. |
| `gemini_cleanup(request: CleanupRequest)` | Same name, different request shape. Verify ownership rules, status/run/cancel, and durable scope independently; do not replay jobs. |

No listed facade preserves its released request signature. Do not migrate clients by flattening arguments mechanically. Check response envelopes, typed errors, action annotations, read-back verification and same-account identity. Validate unknown or in-progress operation handling without replaying external writes. Preserve old state files and existing handles until migration is proven.

## Released action literals

72 source literal occurrences (40 distinct spellings); nested subrequests and same action names across models are intentionally kept separate. Occurrence counts are not tool counts or tested capabilities.

| Released request model | Exact action literal(s) |
| --- | --- |
| [`HistoryList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L21) | `list` |
| [`HistorySearch`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L25) | `search` |
| [`HistoryRead`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L33) | `read`, `export` |
| [`HistoryDelete`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L40) | `delete` |
| [`HistoryUpdate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L45) | `update` |
| [`HistoryTaskStatus`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L58) | `task_status` |
| [`NotebooksList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L68) | `list` |
| [`NotebooksChats`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L73) | `chats` |
| [`NotebooksMove`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L78) | `move` |
| [`NotebooksGet`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L85) | `get`, `sources` |
| [`NotebooksCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L91) | `create` |
| [`NotebooksUpdate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L98) | `update` |
| [`NotebooksDelete`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L112) | `delete` |
| [`NotebooksAddSource`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L119) | `add_source` |
| [`NotebooksRemoveSource`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L137) | `remove_source` |
| [`ScheduledList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L151) | `list` |
| [`ScheduledGet`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L158) | `get`, `delete` |
| [`ScheduledCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L164) | `create_daily` |
| [`ScheduledNativeCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L217) | `create` |
| [`ScheduledUpdate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L227) | `update` |
| [`ScheduledToggle`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L244) | `pause`, `resume` |
| [`GemsList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L256) | `list` |
| [`GemsCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L260) | `create` |
| [`GemsUpdate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L267) | `update` |
| [`GemsDelete`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L281) | `delete` |
| [`PromptsList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L289) | `list`, `categories` |
| [`PromptsGet`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L294) | `get`, `delete` |
| [`PromptsCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L299) | `create` |
| [`PromptsUpdate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L307) | `update` |
| [`PromptsRender`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L322) | `render` |
| [`AccountRead`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L333) | `status`, `models`, `capabilities` |
| [`AccountPage`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L337) | `links`, `library`, `modes` |
| [`AccountUsage`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L341) | `usage` |
| [`AccountFeatures`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L346) | `features` |
| [`NativeSkillsList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L354) | `list` |
| [`NativeSkillsTarget`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L358) | `get`, `enable`, `disable`, `delete` |
| [`NativeSkillsCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L363) | `create` |
| [`NativeSkillsUpdate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L370) | `update` |
| [`NativeSkillsUpload`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L384) | `upload` |
| [`AccountSkills`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L395) | `skills` |
| [`SharingList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L400) | `list` |
| [`SharingCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L404) | `create` |
| [`SharingDelete`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L413) | `delete` |
| [`AccountSharing`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L421) | `sharing` |
| [`LibraryList`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L426) | `list` |
| [`LibraryTarget`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L433) | `get`, `delete` |
| [`AccountLibrary`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L443) | `library_assets` |
| [`CanvasRead`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L448) | `get`, `versions` |
| [`CanvasChatArtifacts`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L453) | `chat_artifacts` |
| [`CanvasCreate`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L459) | `create` |
| [`CanvasEdit`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L466) | `edit` |
| [`CanvasExport`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L472) | `export` |
| [`AccountCanvas`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L478) | `canvas` |
| [`CleanupStatus`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L489) | `status` |
| [`CleanupRun`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L494) | `run` |
| [`CleanupCancel`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L499) | `cancel` |
| [`CleanupTests`](https://github.com/Luckycat133/gemini-web-mcp/blob/362a368f6c68b01c6eb143586db45743c5222e2c/src/domain/account_requests.py#L504) | `test_artifacts` |
