# Competitive Research — Current Contract

This is the current product contract for Agent Investigator's
`competitive_research` workflow. The filename is retained for existing links.
Historical implementation notes are indexed in [docs/README.md](README.md).

## Scope and approval

- Owner-scoped investigations; organization-wide collaboration is not yet the
  product contract.
- Two to five competitors and one to twelve comparison dimensions.
- Explicit `required_dimensions` must be a subset of the comparison dimensions.
  Other gaps may remain documented without making the whole report incomplete.
- Scope approval precedes collection; report approval precedes publication.
- Official domains and repository roots are shown at scope approval.
  A shared hosting domain such as github.com does not establish repository
  ownership; Issue, forum and user-content pages do not become official product
  documentation merely by sharing an approved host.

## Execution

Planning and report routing use ordinary DeerFlow Runs. Collection and analysis
use durable batches scoped to competitors. Audit uses ordinary Runs with groups
of at most four Claims. Rework can collect missing evidence, investigate conflicts,
revise/split a proposition or retire it; it reuses the same runtime.

Application code owns stage transitions:

`planning → awaiting_scope_approval → collecting → normalizing → analyzing →
auditing → reworking (when required) → synthesizing →
awaiting_publish_approval → published`.

Failures and cancellation have explicit terminal states. Business rework is
bounded to two rounds. Optional gaps and non-blocking notes do not cause
unnecessary automatic rework; missing competitor facts and required dimensions do.

## Evidence and claims

- Search is generated per competitor and dimension. Full text is fetched before
  candidate selection, and admission decisions plus snapshots are persisted.
- Candidate selection is balanced across dimensions. Each collector receives a
  bounded set; evidence application considers at most ten candidates per
  submission. Counts alone do not prove coverage.
- The stored excerpt must match the retained snapshot. Fact bindings retain
  the exact quote, offsets and snapshot hash; invented numbers and mismatched
  prices are rejected.
- Analysis uses competitor-scoped retrieval and atomic
  subject/predicate/object/conditions statements.
- Price observations include amount, currency, billing period and source quote.
  Official provenance is derived from approved sources, not trusted from a
  model's flag.
- Only verified, entailing bindings count toward support. Conflicting evidence
  stays visible. Retired claims retain history and cannot silently reappear.

## Source-aware confidence

`confidence.py` owns sufficiency and attribution:

| Basis | Meaning |
| --- | --- |
| `official_documented` | A verified official source states a narrowly scoped product fact or price |
| `vendor_stated` | Vendor assertion; not independently established performance |
| `user_reported` | Attributed individual feedback; not prevalence or a universal product fact |
| `corroborated` | Supporting sources satisfy the corroboration policy |
| `unverified` / `legacy_unverified` | Not eligible as a confirmed conclusion |

There is no universal two-domain requirement. Multiple official domains are
not independent verification by themselves. Source scores are heuristic
screening signals, not calibrated correctness probabilities.

Blocking audit issues prevent the affected Claim from publication; ordinary
notes do not freeze unrelated claims. An old issue must be explicitly resolved,
not dropped because a later audit omitted it.

## Report contract

The model selects eligible Claim IDs and may propose labelled hypotheses with
premises and validation actions. The server renders factual text and comparison
tables; arbitrary model-authored factual Markdown is not accepted.

Reports expose separate completion and coverage state:

- `completed`: core research requirements are satisfied.
- `completed_with_gaps`: usable product facts exist for every competitor, while
  optional questions or notes remain visible.
- `incomplete`: execution was interrupted, a competitor lacks basic product
  facts, a required dimension is missing, or a blocking report issue remains.

The eleven-section normal report can contain known gaps. No opportunity
hypothesis is an acceptable outcome. Interrupted runs produce concise partial
results rather than duplicating all Claims across report chapters.

Publication rechecks Claim versions, source-basis snapshots and current core
requirements. Web, Markdown and server-rendered PDF preserve attribution.

## Budgets and recovery

Investigations have a thirty-minute deadline and a 300K–525K token budget scaled
by competitor count. Stages reserve capacity before dispatch. Non-audit/report
stages leave twenty percent of capacity for completion work.

Text request preflight conservatively bounds input and caps output before a
model call. Unknown usage remains charged or reserved. Research batches use one
execution attempt; workflow recovery/rework obtains fresh budget. Ordinary Runs
retain initial-plus-two attempts per protocol task.

Stable task inputs, submissions, reservations and execution keys are durable.
See [orchestration and recovery](COMPETITIVE_RESEARCH_MULTI_AGENT_ORCHESTRATION.md)
for lease and replay semantics.

## Storage, deployment and verification

The independent migration chain is `alembic_version_ci`, currently through
`ci_0011`. `ci_0010` adds candidates, budget reservations and submission aliases;
`ci_0011` adds source basis, approved repositories and required dimensions.

Development can use SQLite, local artifacts and development providers.
Production requires PostgreSQL, DB Run Events, Redis StreamBridge, durable
batches, configured search/extraction providers, semantic embeddings and
S3-compatible storage. Dynamic-page browser extraction must use the guarded
Playwright path. The supplied Compose overlay provides PostgreSQL and MinIO
alongside the existing stack.

These implementations do not substitute for a live production acceptance run.
Current offline results and limitations are recorded in
[source policy and workspace validation](COMPETITIVE_RESEARCH_CONFIDENCE_AND_WORKSPACE_ZH.md).
The historical Bilibili run did not complete Audit.
