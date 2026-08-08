# How to Actually Experience the Product

This is the shortest path from “the repository builds” to “an agent can really use it.” Use a reviewed commit SHA when reproducibility matters.

## 1. Prove Installation and MCP Stdio Without Credentials

Replace the example value before running:

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding
```

Expected result: JSON reporting `status=ok`, `mode=offline`, `credentials_accessed=false`, a negotiated protocol version, server version, and a non-zero `model` profile tool count.

This proves installation, entrypoint resolution, stdio transport, MCP negotiation, and a real tool call. It does not prove Gemini authentication or model access.

## 2. Prove Live Text Explicitly

Configure the account cookies in the shell or client environment, replace the example SHA, then run:

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
uvx --from "$SOURCE" gemini-mcp-onboarding chat \
  --allow-live-account \
  --prompt "Reply with exactly: gemini-mcp-live-ok" \
  --model flash \
  --thinking-level standard
```

Inspect both returned text and the structured domain result. Confirm the request was temporary or that its cleanup/retention state is visible.

## 3. Prove a Verifiable Local Image

```bash
REVIEWED_SHA=replace-with-reviewed-40-character-commit
SOURCE="git+https://github.com/Luckycat133/gemini-web-mcp@${REVIEWED_SHA}"
mkdir -p /tmp/gemini-mcp-images
uvx --from "$SOURCE" gemini-mcp-onboarding image \
  --allow-live-account \
  --prompt "A clean blue circle on a white background" \
  --output-dir /tmp/gemini-mcp-images
```

The command should fail unless it receives a local image inside the requested directory with non-zero size, image MIME type, positive dimensions, and `verification.status=verified`.

## 4. Connect a Real Agent Client

Start with the smallest profile. Replace `REVIEWED_COMMIT_SHA` with the reviewed 40-character commit before copying the configuration into the client:

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

Use `core` for images, video, music, files, URLs, and research. Use `gemini-mcp-skill-server` when the fixed compact facade is the desired discovery experience. Use `all` only for maintainer verification.

Good client prompts:

```text
Use Gemini to critique this answer, then summarize where the two models disagree.
```

```text
Ask Gemini to generate an image, save it locally, and report the verified artifact path and dimensions.
```

```text
Start Deep Research without waiting. Return the operation/chat identifiers and tell me how the result can be recovered.
```

Observe whether the client discovers the intended tool without needing repository-specific coaching, displays artifacts usefully, and preserves structured errors.

## 5. Experience the Full Multimodal Surface From Source

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

Then use an MCP client to run, in order:

1. text and a multi-turn session;
2. image generation and image editing with a reference file;
3. video generation;
4. music generation;
5. local file analysis;
6. URL analysis;
7. Deep Research with both wait and start-only modes;
8. history list/search/read/export, followed by a marked temporary chat deletion when authorized;
9. a disposable Gem or scheduled-action mutation with read-back;
10. cleanup of all marked artifacts.

## 6. What to Inspect, Not Just What to Read

For every result check:

- `ok`, error code, retryability, and operation state;
- requested, request, effective, and observed backend fields;
- session/operation/continuation IDs;
- artifact state, path/URI, MIME, size, dimensions or duration;
- mutation verification status;
- lifecycle and cleanup state;
- whether text agrees with structured content.

For video/audio, play the artifact. For files, open the saved path. For history or mutations, read back the authoritative state. A chat deletion is verified only when complete fresh history-metadata pagination produces `verification.status=verified_absent` and `data.deleted=true`; `read_chat(None)`, accepted-but-unverified, still-present, and read-back-error results are not proof of deletion. For long operations, verify that a timeout still leaves enough information to recover later.

## 7. Record Friction as Product Evidence

During actual use, note:

- installation time and first successful call;
- client-specific configuration failures;
- tools the agent did not discover naturally;
- ambiguous arguments or result fields;
- artifacts the client could not render or locate;
- long operations that needed a poll/resume workflow;
- account/profile mismatches;
- Gemini Web drift or parser failures;
- whether cleanup and retention behaved as expected.

Convert reproducible friction into a focused issue with client version, commit SHA, profile, tool arguments without secrets, structured result, diagnostic ID, and expected behavior.
