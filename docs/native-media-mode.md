# Native media generation and source-chat cleanup

Updated: 2026-10-02. The new request path is covered offline. Signed-in MCP generation and deletion acceptance for this path are **NOT_RUN**: macOS refused access to the Chrome data directory, and subsequent browser control stopped at the locked Mac. Earlier successful image/edit/music calls are dated separately in [live UI coverage](live-ui-coverage.md).

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

This establishes the static selector-to-request chain. Companion fields 55/56 (array indexes 54/55) carry different selections; their IDs must not be substituted for native media modes. Current account companion defaults, actual wire acceptance and resulting artifacts remain live checks. No exclusive image-model endpoint or header switch was established.

## Artifact acceptance

Primary `gemini_generate_media` / `gemini_generate_music` and compact `create` / `edit` share `src/services/media_generation.py`. Compact saves in the server's working-directory `generated_media/`; primary also accepts an explicit output directory and filename.

Creation accepts generated images, matching video, and music audio. A search `WebImage` or text-only answer cannot satisfy image creation. Files must pass format and decoding/stream verification; missing decoders produce unverified/partial results. Queued, remote-only, empty, partial-save and timed-out results retain distinct states. An explicitly queued operation can expose a verified local artifact without becoming completed. Output identity is derived from the originally observed URI even when the upstream saver upgrades a preview URL to full-size bytes.

Destination paths are reserved atomically, and music downloads suppress unrequested SDK thumbnails on a request-local copy. A failed music read-back, rejected envelope, or malformed observed music card is a recovery failure; only a matching, structurally valid empty read-back establishes empty output.

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
