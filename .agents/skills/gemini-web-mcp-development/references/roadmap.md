# Development and Validation Roadmap

## Settled Boundaries

- One repository, shared services and two business surfaces: `gemini-mcp-function` and `gemini-mcp-account`.
- The function catalog covers assistance, sessions, media, research and operation recovery; the account catalog covers explicitly requested account work.
- No `GEMINI_TOOLS` means the function surface. Explicit profiles, assist and compact remain compatibility routes.
- Function media always starts in the background; preserve the same handle for recovery. Cancellation is not implemented.
- Account `gemini_cleanup` is restricted to the MCP-owned ledger and does not match titles.

## Validation Packages

1. Verify exact function/account catalogs, annotations, read/write semantics and package entrypoints.
2. Exercise default background start, interruption, status/result and verified Artifact recovery without duplicate requests.
3. Check cleanup ownership and read-back; protect unfinished research and media.
4. Reconcile onboarding, client examples, Runtime Skills, evaluations and compatibility profiles.
5. Run offline and package/protocol gates, then authorized live checks separately.

## Remaining Owner Choice

The officially supported client/OS/distribution matrix remains an owner decision. Record actual evidence rather than extrapolating from one account or a fixture.
