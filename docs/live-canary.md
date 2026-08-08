# Live Gemini Web compatibility canary

The live canary is a separate, opt-in GitHub Actions workflow for detecting
Gemini Web transport and parser drift. Pull-request CI, unit tests, package
smokes, and release verification remain offline and never require Gemini
credentials.

## Safety boundary

The workflow runs only when all of these controls are true:

1. the repository variable `GEMINI_LIVE_CANARY_ENABLED` is `true`;
2. the repository variable `GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT` is `true`;
3. a manual run confirms `run_live`, or the opt-in weekly schedule fires;
4. the job enters the `gemini-live-canary` GitHub environment.

Use a dedicated test account. Reverse-engineered Gemini Web access can trigger
account restrictions and must not use a personal or production account. Store
`GEMINI_PSID`, `GEMINI_PSIDTS`, and optionally `GEMINI_PSIDCC` as secrets in the
`gemini-live-canary` environment. Never store Cookie values as repository
variables, workflow inputs, artifacts, logs, or issue text.

The canary executes only the 21 read-only contracts centralized in
`WEB_FEATURE_PROBE_CONTRACTS`. It does not create chats, scheduled actions,
Notebooks, media, or other account artifacts, so it has nothing to clean up.

## Diagnostic contract

Every run writes a report validated by
`compatibility/live-canary-report.schema.json`. The allowlisted fields are:

- repository commit and trigger;
- installed Python, project, `gemini-webapi`, `mcp`, and `mcp-types` versions,
  alongside their reviewed supported ranges;
- Gemini Web build label and locale when the initialized client exposes values
  matching bounded safe formats;
- capability key, RPC id, source path, parser, HTTP status, reject code, body
  count, terminal state, and parser stage;
- stable sanitized error code and exception class name.

The report never includes raw Web responses, exception messages, Cookie values,
session identifiers, chat text, titles, URLs, account identifiers, or generated
content. The workflow's issue publisher builds Markdown from the same explicit
allowlist instead of serializing the whole report.

Probe outcomes have distinct meanings:

| Outcome | Meaning | Workflow result |
|---|---|---|
| `available` / `empty` | RPC envelope and registered parser are compatible | healthy |
| `unavailable` | Gemini returned an RPC rejection, commonly account/region capability gating | degraded, non-failing |
| `envelope_drift` | response envelope is unreadable or the matching body disappeared | failing drift |
| `parser_drift` | the RPC body reached its parser but changed shape | failing drift |
| `transport_failed` | initialization, HTTP, timeout, or network path failed | failing operational result |

On a failing result, the workflow opens or comments on the single issue titled
`[live-canary] Gemini Web compatibility drift` before enforcing failure. A later
healthy or degraded run comments on and closes that issue. The sanitized JSON
artifact is retained for 14 days.

## Dependency matrix and evidence

`compatibility/upstream-matrix.json` is the reviewed machine-readable support
matrix. Offline tests ensure its dependency requirements match `pyproject.toml`.
The report records the versions actually installed in each run, so an issue can
identify the exact dependency/Web build/locale/RPC/parser boundary involved.

`fixture_verified` means the code path passed synthetic offline contracts. A
`live_verified` value of `false` means that the repository change itself did not
observe a dedicated account. Do not convert expected routing or fixture results
into a claim of live backend behavior; the workflow artifact is the live
evidence.

## Local checks

This command proves the refusal path and does not access Gemini:

```bash
python scripts/run_live_canary.py --output /tmp/gemini-web-canary.json
```

A maintainer may run the live path only with a dedicated account and all three
controls:

```bash
export GEMINI_LIVE_CANARY_ENABLED=true
export GEMINI_LIVE_CANARY_DEDICATED_ACCOUNT=true
export GEMINI_PSID="<dedicated-account secret>"
python scripts/run_live_canary.py \
  --allow-live-account \
  --output /tmp/gemini-web-canary.json \
  --repository-commit "$(git rev-parse HEAD)"
```

Run the offline canary contracts with:

```bash
python -m pytest -q tests/test_live_canary.py tests/test_ci_contracts.py
```

The live canary is reported separately from release and PR gates. A disabled
workflow is not evidence of compatibility, and a missing live observation must
remain explicit in release notes.
