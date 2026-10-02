# Native media generation and source-chat cleanup

Updated: 2026-10-02. Authorized fresh MCP calls verified primary and compact image generation/editing as decoded, visually checked 2816×1536 JPEG files. The initial SDK 2.0.0 music attempt failed without an artifact or returned source-chat ID. After the recovery repairs, a separate single SDK 2.1.1 music request produced MP3 audio and a companion MP4, both fully decoded at about 122.54 seconds. Targeted follow-up verified all five successful requests' owned source chats absent, with local files intact. Exact evidence and earlier observations are dated separately in [live UI coverage](live-ui-coverage.md).

## Request selection

Image creation/editing explicitly selects Gemini Web's image feature; music selects its music feature. This avoids relying solely on the chat model to interpret a generation prompt. The service still supplies the existing generation instruction and files. Google controls backend dispatch, availability and final output; an explicit feature selector cannot establish successful generation by itself.

| Creation request | Web feature mode | Serialized StreamGenerate array |
| --- | --- | --- |
| Image, including reference-image edit | 14 | `message[49] = 14` |
| Music | 21 | `message[49] = 21` |
| Generic video compatibility argument | No new selector verified | Existing route; require a video artifact |

`src/infrastructure/web_request_contracts.py` owns these observed selector constants. `ThinkingLevelGeminiClient` injects them into the existing upstream `f.req` envelope under request-local context. They are consumed internally, never passed as unknown HTTP arguments. Missing, changed or conflicting payload shapes raise `UPSTREAM_CHANGED` before sending the generation request, without silently retrying ordinary chat. The selector coexists with thinking level and is cleared after the request; learning companion modes remain separate.

The selected chat model header is unchanged by these selectors. Google's [image help](https://support.google.com/gemini/answer/14286560) currently maps Flash-Lite to Nano Banana 2 Lite, Flash/Pro initial creation to Nano Banana 2, and Pro redo to a separate UI action. Google [announced Lyria 3.5](https://blog.google/innovation-and-ai/products/gemini-app/better-tracks-lyria-gemini/) for music. These product descriptions do not supply a backend identifier for a particular MCP response; `observed_backend` remains empty without response evidence.

## Public source evidence

Exact public URLs, byte sizes and SHA256 values are recorded in [the source manifest](../compatibility/native-media-source-20261002.json). The main and bootstrap URLs were observed in Chrome DOM. The remaining module IDs were explicitly referenced by that public source and loaded using its documented loader structure. No raw account response or private request is included.

The observed build is `BardChatUi.zh_CN.sOs2tZuLK_8.2018.O`:

1. `toolbox-with-dependencies.js:7028` defines image `featureMode:14` and music `featureMode:21`; labels occur at lines 7026–7027.
2. `toolbox-module.js:61,92` publishes the selected object as `featureModeDataChange`. `input-v2-module.js:49,225` sends its integer through `Ry` to ConversationService `Lm`.
3. `main-module.js:4498,1645,4443,1584,4488` carries `UpdateSelectedFeatureMode` into the store selector `Yg`. Lines 4476–4482 construct request `jf` from that selection.
4. `main-module.js:3878` sends request `jf` through `EOd` into JSPB field 50. The ordinary-array setter in `frontend.js:52,123` and `main-module.js:78` places field 50 at zero-based index 49.

This establishes the static selector-to-request chain. Companion fields 55/56 (array indexes 54/55) carry different selections; their IDs must not be substituted for native media modes. Bounded image/edit calls and a fresh SDK 2.1.1 music call have live artifact evidence. No exclusive image-model endpoint or header switch was established.

## Artifact acceptance

Primary `gemini_generate_media` / `gemini_generate_music` and compact `create` / `edit` share `src/services/media_generation.py`. Compact saves in the server's working-directory `generated_media/`; primary also accepts an explicit output directory and filename.

Creation accepts generated images, matching video, and music audio. A search `WebImage` or text-only answer cannot satisfy image creation. Files must pass format and decoding/stream verification; missing decoders produce unverified/partial results. Queued, remote-only, empty, partial-save and timed-out results retain distinct states. An explicitly queued operation can expose a verified local artifact without becoming completed. Output identity is derived from the originally observed URI even when the upstream saver upgrades a preview URL to full-size bytes.

Destination paths are reserved atomically, and music downloads suppress unrequested SDK thumbnails on a request-local copy. A failed music read-back, rejected envelope, or malformed observed music card is a recovery failure; only a matching, structurally valid empty read-back establishes empty output.

## Interrupted generation

Each media attempt uses a blank, request-owned SDK session with isolated
metadata. A valid chat ID observed in the stream is retained through SDK
metadata rollback and the total request deadline. Failure responses expose
that `source_chat_id` for recovery and retain the chat; missing metadata does
not prove that no chat was allocated. An existing chat handle cannot enter this
ownership path, and a native request does not retry generation automatically.

SDK `APIError` is classified as a stable, safe failure instead of an internal
implementation error. An SDK-reported possible interruption uses
`UPSTREAM_REJECTED`; an SDK response-parser failure uses `UPSTREAM_CHANGED`.
The message does not claim a confirmed Google rejection or quota cause, and
arbitrary upstream response text is not exposed. Primary and compact adapters
share this classification and recovery evidence.

Installation requires `gemini-webapi>=2.1.1,<3`. Offline regressions also cover
the earlier SDK 2.0.0 request builder and stream parser. SDK 2.1.1 can recover
a known chat without a final stream marker
and parse sparse music-card field 87; SDK 2.0.0 lacks those paths. These
synthetic differences do not establish the cause of the failed live request.
For SDK versions supporting `extended_thinking`, the wrapper passes that
supported argument so model header and body agree; older SDKs never receive it
as an unknown HTTP argument.

## Cleanup policy

Only a chat ID returned by this request, started without an existing chat, qualifies for automatic generation cleanup. Invalid or missing IDs cannot trigger an account scan or deletion. Account changes cancel cleanup belonging to old authentication.

| Result / caller setting | Source-chat policy |
| --- | --- |
| Operation finished with every output saved locally and verified | Immediately attempt verified deletion |
| Definitive empty output, including text-only or search-only image response | Immediately attempt verified deletion |
| Queued, unsaved remote output, unverified output, partial save, failed recovery read or interrupted operation | Retain for recovery |
| Primary `retain_chat=true` | Retain |
| Primary positive `delete_after_seconds` | Schedule that delay in memory |

Deletion has a 10-second caller-wait budget, separate from the generation/save deadline. The result includes `_meta.domain_result.meta.details.cleanup`: only `completed` / `already_completed` establishes positive absence read-back. A pending, failed or cancelled observation cannot establish deletion and does not invalidate a verified local artifact. A still-running worker may finish later while the process remains alive. Positive delayed jobs are not durable across process exit.

Automatic due work rechecks its current job, deadline and attempt before claiming deletion. Retaining or replacing a job after a batch snapshot prevents that stale batch from deleting it.
Retention cancels unclaimed scheduled work; it cannot retract an explicit deletion task already accepted by the manager.

For disposable live checks, record each exact returned chat ID immediately, inspect the file before accepting cleanup, and follow up any unverified deletion. If the tester explicitly discards an unsaved/queued test result, delete that known test chat and verify absence. Do not delete regular user chats by generated title or broad history matching. See [the manual checks](manual-testing.md#4-媒体生成gemini_generate_media--gemini_generate_music) and [verification contracts](agent-verification.md).
