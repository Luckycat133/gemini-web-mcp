# Creation Examples And Routing Cases

Calls below use the seven registered tools. Paths/operation handles must come from the user's file or the preceding result.

```json
{"tool":"gemini_generate_image","arguments":{"prompt":"Draw a clean isometric greenhouse","model":"flash"}}
{"tool":"gemini_edit_image","arguments":{"prompt":"Make the roof blue","image_path":"/absolute/input.png"}}
{"tool":"gemini_generate_video","arguments":{"prompt":"A five-second camera move through a greenhouse","retain_chat":false,"idempotency_key":"new_video_request_opaque_token"}}
{"tool":"gemini_generate_music","arguments":{"prompt":"Instrumental jazz for a greenhouse tour","thinking_level":"extended","idempotency_key":"new_music_request_opaque_token"}}
{"tool":"gemini_get_operation_status","arguments":{"operation_id":"<returned operation_id>"}}
{"tool":"gemini_get_operation_result","arguments":{"operation_id":"<returned operation_id>"}}
{"tool":"gemini_cancel_operation","arguments":{"operation_id":"<returned operation_id>"}}
```

Positive triggers: “做张海报”; “Turn this local picture into a watercolor”; “Make a short video”; “Write and generate an instrumental soundtrack”; “The music request timed out, recover its operation”.

Near misses: “What is wrong in this screenshot?” -> gemini-assist understanding; “Write a prompt for a music generator” -> assistance/text; “Delete old generated Gemini chats” -> explicit gemini-account; “Implement the music tool” -> development.

## Output and continuation

Use a returned verified local path in the requested document, app or media
project. A queued or partial result can contain a file without completing all
requested outputs; preserve its operation handle until that run is resolved.
For an edit, retain the input and use the distinct edited output.

Model/thinking options come from the connected tool's schema. Request aliases
and selected Web modes do not establish the actual provider model version.

When using start idempotency, replace the example key with a fresh opaque token
and preserve it before the call. Reuse it only to look up that request after
response loss. Recover a returned handle through status/result; create another
variant when the user requests new work, with a new key.

Handles and locators persist in the server's private, authentication-scoped
metadata for seven days by default. Keep the same state location and account
setup for continuation. Expired or unavailable handles are reported failures;
they do not authorize scanning unrelated history. Generated files are separate.

## Cancellation and source retention

`cancel_requested` is best effort. `local_cancelled_before_start` proves only
local cancellation before provider submission; provider cancellation needs its
own positive evidence.

Successful saved output and source cleanup have separate states. The shared
service cleans its newly created chat after verified completion or definitive
empty output. Queued, partial, remote-only or interrupted work retains recovery.
`retain_chat` and `delete_after_seconds` change that policy. Registered delayed
jobs survive restart; use the account cleanup facade when authorized to inspect
or act on them. A cleanup failure does not invalidate an already verified file.

Reproducible offline call/schema and expected-route cases live in evaluations/focused_skill_cases.json. These fixtures do not claim measured LLM trigger accuracy or live generation.
