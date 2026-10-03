# Tools, Skills and agent handoffs

Read when changing a public tool, Skill discovery, structured results or an
agent-use evaluation. Source schemas and the architecture reference own the
API; this reference covers task selection and completion.

## Discovery and composition

Describe the user outcome in the Skill description. Keep the overlapping
intents clear: analyzing an image uses assist, generating/editing media uses
create, requested history/organization uses account, and repository changes
use development. A mixed task may need several capabilities in sequence.

Keep the entrypoint focused on task choice and non-obvious operating contracts.
Use references for substantial conditional mechanics or examples. Short
`default_prompt` metadata is a usable invocation example, not another copy of
the instructions. Account detail is loaded when the task needs it.

Maintain one source for API names and contracts. Prefer distinct tools when
intent, schema or completion differs; preserve established compatibility names
and delegate shared execution to services. Avoid adding a tool merely for each
Web UI control.

## Results that continue the task

Assistance supplies information to incorporate into the agent's work. Sourced
search distinguishes observed evidence from a source-free answer. Mixed inputs
keep IDs and outcomes so missing evidence is visible.

Media and completed reports supply artifacts. Preserve their identity,
requested kind, readiness and verification, then use them at the requested
destination. Technical fields support acceptance; they need not become a long
user-facing receipt.

Long work exposes explicit start/status/result/cancel semantics. Recover the
same handle across interruption and distinguish cancellation requests from
confirmed cancellation. Account facades distinguish reads from mutations and
require positive read-back before declaring a target changed.

## Evaluation

Use realistic user requests with minimal raw inputs/results. Check actual
choices and deliverables. Useful close alternatives include:

- screenshot diagnosis versus editing its pixels;
- writing a music prompt versus generating audio;
- sourced web search versus searching private Gemini history;
- cleaning local generated files versus deleting account conversations;
- a new media variant versus recovery of an existing operation;
- a composite request that searches, generates, then uses an artifact.

Exercise paraphrases, mixed languages and interruptions when those risks are
relevant. Existing offline call/schema cases live in
`evaluations/focused_skill_cases.json`; they are expected-route fixtures, not a
measured trigger score. For an independent forward test, provide the task and
raw evidence without the proposed answer or prior review conclusions.

Record task completion, scope, tool calls, duplicate starts, source/handle
continuity, structured evidence and downstream use. Retain only the evidence
needed to understand a demonstrated gap; fix its shared cause before adding
another instruction to the Skill.
