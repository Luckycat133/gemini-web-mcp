# How to Actually Experience the Product

Use this reference to move from “the repository builds” to “an agent can complete real Gemini Web workflows.” Pin a reviewed commit whenever reproducibility matters.

## Understand the Three Distribution Surfaces

- **Python package/server:** installs `gemini-mcp-server`, `gemini-mcp-skill-server`, and `gemini-mcp-onboarding`.
- **Repository development skill:** instructs an engineering agent how to modify this repository.
- **ClawHub runtime skill:** `clawhub install gemini-web-mcp` installs operating instructions for an already available runtime path; verify the public listing before claiming a repository change is published.

These surfaces retain distinct roles and licenses, but the active repository version is unified at `0.2.1`. The existing `v0.2.0` tag remains immutable; new release refs use `0.2.1`.

Do not use a runtime-skill installation as proof that the Python server package or a GitHub Release was installed.

## 1. Credential-Free Installation and MCP Stdio Preflight

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding
```

Expected JSON includes:

```text
status=ok
mode=offline
credentials_accessed=false
protocol_version=<negotiated version>
server_version=<installed package version>
enabled_tools>0
```

This proves installation, entrypoint resolution, stdio transport, MCP negotiation, and a real auth-free tool call. It does not prove Gemini authentication or model access.

## 2. Explicitly Authorized Live Text

Configure account Cookies in the process or client environment, then run:

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding chat \
  --allow-live-account \
  --prompt "Reply with exactly: gemini-mcp-live-ok" \
  --model flash \
  --thinking-level standard
```

Inspect both text and the structured domain result. Confirm:

- `ok=true` and terminal operation state;
- requested/effective/observed backend fields are not conflated;
- temporary/retention behavior is visible;
- any created remote chat ID is recorded for direct cleanup.

## 3. Independently Verified Local Image

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
mkdir -p /tmp/gemini-mcp-images
uvx --from "$SOURCE" gemini-mcp-onboarding image \
  --allow-live-account \
  --prompt "A clean blue circle on a white background" \
  --output-dir /tmp/gemini-mcp-images
```

The command should fail unless it receives a local image inside the requested directory with:

- an existing non-empty file;
- image MIME type;
- positive width and height;
- artifact `state=local`;
- `verification.status=verified`.

Open the file rather than trusting response prose.

## 4. Connect a Real MCP Client

Replace the commit placeholder before copying this configuration:

```json
{
  "mcpServers": {
    "gemini": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/Luckycat133/gemini-web-mcp@REVIEWED_COMMIT_SHA",
        "gemini-mcp-server"
      ],
      "env": {
        "GEMINI_TOOLS": "model"
      }
    }
  }
}
```

Profile guidance:

- `model`: smallest text/session starting surface;
- `history`: typed history workflows;
- `history-organize`: history plus Notebook organization;
- `account-read`: read-only account inventory;
- `core`: text, media, files, URLs, and research;
- `scheduled-admin`: explicitly authorized scheduled mutations;
- `all`: maintainer verification only;
- `gemini-mcp-skill-server`: fixed eleven-tool compact discovery surface.

Useful client prompts:

```text
Use Gemini to critique this answer, then summarize where the two models disagree.
```

```text
Ask Gemini to generate an image, save it locally, and report the verified path, MIME type, size, width, and height.
```

```text
Start Deep Research without waiting. Return every operation or chat identifier and explain how to recover the result.
```

Observe whether the client selects the right tool without repository-specific coaching, preserves structured errors, and renders or locates artifacts usefully.

## 5. Full Multimodal Experience From Source

```bash
git clone https://github.com/Luckycat133/gemini-web-mcp.git
cd gemini-web-mcp
python -m venv .venv
. .venv/bin/activate
pip install -e ".[all,dev]"

python scripts/smoke_profiles.py
python scripts/smoke_mcp_protocol.py
GEMINI_TOOLS=core gemini-mcp-server
```

Run these workflows in order:

1. one-shot temporary text;
2. primary and compact multi-turn sessions;
3. image generation and reference-image editing;
4. video generation;
5. music generation;
6. local-file analysis;
7. URL analysis;
8. Deep Research in start-only and wait modes;
9. history list/search/read/export;
10. a disposable Gem, scheduled action, or Notebook move with read-back;
11. direct-ID cleanup for every created resource.

## 6. Modality Acceptance Matrix

| Workflow | Minimum proof |
| --- | --- |
| Text | terminal structured result plus expected text |
| Session | context preserved across turns; reset affects only the selected MCP/Gemini session |
| Image | verified local file or usable remote URI; MIME, size, and dimensions |
| Video | playable artifact; MIME, size, duration when observable, and terminal/queued state |
| Music | playable artifact; MIME, size, duration when observable, and terminal/queued state |
| File | source artifact identified and structured analysis result returned |
| URL | requested URL preserved and structured webpage/analysis state returned |
| Deep Research | operation/chat IDs, queued/running/completed/timed-out state, and result recovery path |
| History | typed records, explicit pagination, and private-turn scanning only when authorized |
| Mutation | authoritative read-back and no success text for ambiguous evidence |
| Cleanup | every created ID accounted for; `verified_absent` when claiming deletion |

## 7. Long-Operation Friction to Record

Until the shared `start/status/result/cancel` API exists, record:

- which tool started the operation;
- returned provider/research/chat IDs;
- whether the call timed out or returned queued/running;
- how the result was recovered;
- whether another client/process could resume it;
- whether cancellation was requested and what was actually observed;
- artifact identity from initial request through final result.

This evidence should shape the shared operation service rather than creating modality-specific polling tools independently.

## Shared Long-Operation Experience Target

After the full live baseline, implement one local SQLite-backed flow:

```text
start -> operation_id
status(operation_id)
result(operation_id)
cancel(operation_id)
```

Users must be able to restart the server and resume from another MCP client. Keep recovery metadata for seven days by default, preserve provider IDs and artifact identity, and report cancellation as best effort unless the provider confirms it. The database never stores prompts, chat text, Cookies, or raw responses.

## 8. Cleanup and History Verification

Record every returned remote resource ID immediately. Gemini-generated titles may omit prompt markers, so marker search is only a fallback.

A chat deletion is verified only when a complete fresh authoritative history-metadata read-back produces `verification.status=verified_absent` and the structured result reports deletion. These are not proof:

- accepted upstream delete response alone;
- `read_chat(None)`;
- zero marker-search results when titles omitted the marker;
- incomplete pagination;
- read-back error;
- still-present state.

## 9. Record Product Friction

For each real client test, record:

- client/version, OS, Python version, protocol mode, commit SHA;
- install time and first successful call;
- profile and tool selected by the agent;
- arguments without credentials or private content;
- structured result, diagnostic ID, and compatibility text;
- artifact path/URI and renderability;
- timeout and recovery behavior;
- account/entitlement mismatch;
- cleanup and retention outcome.

Turn reproducible friction into focused issues. Separate client UX failures, project defects, entitlement absence, and Gemini Web drift.
