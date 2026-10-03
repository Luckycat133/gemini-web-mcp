# Planning a development package

Read this reference when choosing new work. The checkout's
`docs/development-status.md` owns current implementation and evidence status;
`docs/client-examples.md` owns the client/OS matrix. Check those and the relevant
source before proposing a package.

The focused assist/create/account surfaces, shared operations, durable cleanup
and compatibility router are existing foundations. Extend the shared owner
rather than rebuilding them as separate products.

## Choose a bounded outcome

| Work area | Useful acceptance evidence |
| --- | --- |
| Provider compatibility | current request/response evidence, typed drift/unavailable state, sanitized parser fixtures |
| Long-operation reliability | preserved handle/source, restart recovery, no duplicate start, final artifact or truthful pending state |
| Account capability | exact target, action schema, pagination/coverage and positive mutation read-back |
| Agent usability | correct intent/tool choice, usable artifact or answer in the downstream task, close-alternative cases |
| Distribution/client support | actual installation, entrypoints, negotiated MCP protocol and stated OS support |

Take the smallest coherent package that completes the user's requested
workflow. A package may span adapters, services, examples and evaluation cases
when the same contract is exposed in each place.

Additional Web features such as Drive import, Canvas, richer recurrence,
Notebook CRUD, sharing or Library management need current upstream evidence and
a clear task benefit. Prioritize them against confirmed gaps in the core
workflows, rather than reproducing the entire Web UI.

For publication, resolve the current repository review/release gates and derive
the new version/tag from package metadata. Historical test totals, release
branches and unfinished provider observations belong in dated project records.
