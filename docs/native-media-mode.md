# Native media generation and source-chat cleanup

Updated: 2026-10-03. Authorized focused MCP calls returned independently decoded image/edit JPEGs, music MP3 plus companion MP4, and a native 1280×720, 10.005-second video MP4. Completed music/video handles returned the same files after a server restart. Across the dated test runs, 19 exactly identified, owned source chats were verified absent and local outputs remained intact. Earlier primary/compact successes, transport failures, and interrupted requests are recorded separately in [live UI coverage](live-ui-coverage.md).

## Request selection

Image creation/editing, video, and music explicitly select their Gemini Web features. This avoids relying solely on the chat model to interpret a generation prompt. The service still supplies the existing generation instruction and files. Google controls backend dispatch, availability and final output; an explicit feature selector cannot establish successful generation by itself.

| Creation request | Web feature mode | Serialized StreamGenerate array |
| --- | --- | --- |
| Image, including reference-image edit | 14 | `message[49] = 14` |
| Music | 21 | `message[49] = 21` |
| Video | 11 | `message[49] = 11`; selected mode alone does not prove generation |

`src/infrastructure/web_request_contracts.py` owns these observed selector constants. `ThinkingLevelGeminiClient` injects them into the existing upstream `f.req` envelope under request-local context. They are consumed internally, never passed as unknown HTTP arguments. Missing, changed or conflicting payload shapes raise `UPSTREAM_CHANGED` before sending the generation request, without silently retrying ordinary chat. The selector coexists with thinking level and is cleared after the request; learning companion modes remain separate.

The selected chat model header is unchanged by these selectors. Google's [image help](https://support.google.com/gemini/answer/14286560) currently maps Flash-Lite to Nano Banana 2 Lite, Flash/Pro initial creation to Nano Banana 2, and Pro redo to a separate UI action. Google [announced Lyria 3.5](https://blog.google/innovation-and-ai/products/gemini-app/better-tracks-lyria-gemini/) for music. These product descriptions do not supply a backend identifier for a particular MCP response; `observed_backend` remains empty without response evidence.

## Public source evidence

Exact public URLs, byte sizes and SHA256 values are recorded in [the image/music source manifest](../compatibility/native-media-source-20261002.json) and [the 2026-10-03 video recheck](../compatibility/native-media-source-20261003.json). The two re-fetched modules have unchanged hashes; the video selector follows the same observed request chain. The main and bootstrap URLs were observed in Chrome DOM. The remaining module IDs were explicitly referenced by that public source and loaded using its documented loader structure. No raw account response or private request is included.

The observed build is `BardChatUi.zh_CN.sOs2tZuLK_8.2018.O`:

1. `toolbox-with-dependencies.js:7028` defines image `featureMode:14`, video `featureMode:11` (lines 7028–7030), and music `featureMode:21`; labels occur at lines 7026–7027.
2. `toolbox-module.js:61,92` publishes the selected object as `featureModeDataChange`. `input-v2-module.js:49,225` sends its integer through `Ry` to ConversationService `Lm`.
3. `main-module.js:4498,1645,4443,1584,4488` carries `UpdateSelectedFeatureMode` into the store selector `Yg`. Lines 4476–4482 construct request `jf` from that selection.
4. `main-module.js:3878` sends request `jf` through `EOd` into JSPB field 50. The ordinary-array setter in `frontend.js:52,123` and `main-module.js:78` places field 50 at zero-based index 49.

This establishes the static selector-to-request chain. Companion fields 55/56 (array indexes 54/55) carry different selections; their IDs must not be substituted for native media modes. Bounded focused calls have live artifact evidence for all four creation workflows. No exclusive generator endpoint or header switch was established. The initial native-video request on 2026-10-03 ended with a possible upstream interruption and no video file; its exact source was subsequently verified absent. A later uninterrupted focused request returned the verified native video described above. Neither observation establishes quota, entitlement, or the dispatched generator version.

## Artifact acceptance

Focused `gemini-mcp-create`, primary `gemini_generate_media` / `gemini_generate_music`, and compact `create` / `edit` delegate to `src/services/creation.py` and shared materialization in `src/services/media_generation.py`. Compact saves in the server's working-directory `generated_media/`; primary also accepts an explicit output directory and filename.

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

Bounded 2026-10-03 focused stdio tests independently decoded image/edit JPEGs,
music MP3 plus companion MP4, and a native 1280×720, 10.005-second video MP4.
Completed music/video handles returned the same files after server restart;
their sources were verified absent. Earlier failed and intentionally interrupted
requests remain separate evidence. See [the dated results](live-ui-coverage.md#2026-10-03-focused-creation-and-native-video-recheck).

Recovery re-probes existing local outputs and reuses matching URI/Artifact
identities instead of repeatedly downloading queued previews. Missing or
invalid files can be downloaded again; partial music downloads only its missing
companion. A reused preview keeps queued/unverified state and source retention.

Initialization has at most three attempts for typed transient connection or
timeout errors. It submits no generation. Native generation and Research
disable the SDK's hidden generation retries, and Research captures streamed
source metadata before rollback. Requesting a completed Research report occurs
at most once per poll workflow; later polling reads existing evidence.

Only a chat ID returned by this request, started without an existing chat, qualifies for automatic generation cleanup. Invalid or missing IDs cannot trigger an account scan or deletion. Cleanup is isolated by the effective credential scope. Different credentials cannot execute an old queue; restoring the same credential material and SQLite identity key can resume it. See [operations and cleanup](operations-and-cleanup.md) for the identity boundary.

| Result / caller setting | Source-chat policy |
| --- | --- |
| Operation finished with every output saved locally and verified | Immediately attempt verified deletion |
| Definitive empty output, including text-only or search-only image response | Immediately attempt verified deletion |
| Queued, unsaved remote output, unverified output, partial save, failed recovery read or interrupted operation | Retain for recovery |
| `retain_chat=true` | Retain |
| Positive `delete_after_seconds` | Persist the delayed job in local SQLite |

Deletion has a 10-second caller-wait budget, separate from the generation/save deadline. The result includes `_meta.domain_result.meta.details.cleanup`: only `completed` / `already_completed` establishes positive absence read-back. A pending, failed or cancelled observation cannot establish deletion and does not invalidate a verified local artifact. An active worker may finish later. Delayed jobs survive process exit, and lease expiry allows a restarted worker to retry deletion with fresh absence read-back. Cleanup runs while a service is running; it cannot execute with all servers stopped.

Automatic due work rechecks its current job, deadline and attempt before claiming deletion. Retaining or replacing a job after a batch snapshot prevents that stale batch from deleting it.
Retention cancels unclaimed scheduled work. After a deletion has been claimed, retention is best effort: an in-flight RPC cannot be retracted; a failed attempt is kept retained instead of being retried.

For disposable live checks, record each exact returned chat ID immediately, inspect the file before accepting cleanup, and follow up any unverified deletion. If the tester explicitly discards an unsaved/queued test result, delete that known test chat and verify absence. Do not delete regular user chats by generated title or broad history matching. See [the manual checks](manual-testing.md#4-媒体生成gemini_generate_media--gemini_generate_music) and [verification contracts](agent-verification.md).
