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

For “create an image and add it to this deck”, finish by passing the verified local path to the deck workflow. A text explanation of the intended image is insufficient. Replace each example start key with a newly created opaque token and preserve it before the call. For a timeout with a returned operation_id, status/result are the only generation workflow calls; no second start.

Reproducible offline call/schema and expected-route cases live in evaluations/focused_skill_cases.json. These fixtures do not claim measured LLM trigger accuracy or live generation.
