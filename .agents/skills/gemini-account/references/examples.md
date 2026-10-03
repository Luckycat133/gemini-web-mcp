# Account Examples And Routing Cases

Use exact IDs provided by the user or returned by authorized reads. Every tool takes a typed request object.

```json
{"tool":"gemini_history","arguments":{"request":{"action":"list","limit":20}}}
{"tool":"gemini_history","arguments":{"request":{"action":"read","chat_id":"<observed chat_id>"}}}
{"tool":"gemini_notebooks","arguments":{"request":{"action":"move","chat_id":"<observed chat_id>","notebook_id":"<observed notebook_id>"}}}
{"tool":"gemini_scheduled","arguments":{"request":{"action":"create_daily","title":"Morning briefing","instructions":"Summarize current engineering news","hour":9,"timezone_name":"Asia/Shanghai"}}}
{"tool":"gemini_gems","arguments":{"request":{"action":"update","gem_id":"<observed gem_id>","description":"Concise engineering critique"}}}
{"tool":"gemini_prompts","arguments":{"request":{"action":"create","name":"Review","content":"Review {code}"}}}
{"tool":"gemini_account","arguments":{"request":{"action":"capabilities"}}}
{"tool":"gemini_cleanup","arguments":{"request":{"action":"status","limit":20}}}
{"tool":"gemini_cleanup","arguments":{"request":{"action":"run","job_id":"<registered job_id>"}}}
```

Positive triggers: “查我的 Gemini 会话”; “Move this chat into my Notebook”; “Create a daily Gemini task at 9”; “Update this Gem description”; “Retry this pending cleanup job”.

Near misses: “Look up current web sources” -> gemini-assist; “Generate a logo” -> gemini-create, without history reads; “Review the account service code” -> development. “Clean up the generated files in this local project” is local filesystem work, not Gemini account deletion.

A cancellation request never establishes that an already submitted deletion was undone. A bounded history page that does not contain an ID never establishes verified absence.

## Action details

`create_daily` requires title, instructions and hour (0–23). Supply the intended
IANA `timezone_name`; omission uses the server's `Asia/Shanghai` default.
Gem/Prompt updates leave omitted fields unchanged. Prompt rendering substitutes
variables in the local library and does not generate content remotely.

Cleanup `status` addresses jobs registered in the current authentication scope.
`run` with `job_id` targets that job; omission executes due registered jobs.
Only positive `verified_absent` evidence confirms deletion. The separate
`test_artifacts` action scans bounded metadata by marker and defaults to a dry
run; a broad marker is not proof that every match belongs to the requested task.

Operation metadata and cleanup jobs use the server's private local database;
IDs, states and locators persist there, while credentials and conversation
contents are excluded. Output files and the local Prompt library are separate
stores. Read the connected action schema when options or coverage differ.

Reproducible offline call/schema and expected-route cases live in evaluations/focused_skill_cases.json. These fixtures do not claim measured LLM trigger accuracy or live account mutations.
