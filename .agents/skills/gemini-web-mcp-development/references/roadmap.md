# Development and Validation Roadmap

## Settled Boundaries

- One repository and shared services support two independent MCP entries, `gemini-mcp-function` and `gemini-mcp-account`.
- The default catalogs have six tools each. Function owns generation and operation recovery. Account owns explicit conversation and resource management and read-only Task metadata.
- No `GEMINI_TOOLS` means the function surface. `function-full` and `account-full` expose the prior catalogs. Assist, compact and other legacy profiles remain compatibility routes.
- Function media always starts in the background; preserve the same handle for recovery. Cancellation is not implemented.
- Full-account `gemini_cleanup` is restricted to the MCP-owned ledger.

## Validation Packages

1. Verify exact function/account catalogs, annotations, read/write semantics and package entrypoints.
2. Exercise default background start, interruption, status/result and verified Artifact recovery without duplicate requests.
3. Check cleanup ownership and read-back; protect unfinished research and media.
4. Reconcile onboarding, client examples, Runtime Skills, evaluations and compatibility profiles.
5. Run offline and package/protocol gates, then authorized live checks separately.

## Remaining Owner Choice

The officially supported client/OS/distribution matrix remains an owner decision. Record actual evidence rather than extrapolating from one account or a fixture.
