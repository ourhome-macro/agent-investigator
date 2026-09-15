# Competitive Research V1

This document is the implementation contract for the first vertical Deep
Research product built on DeerFlow. V1 supports only
`competitive_research`; future investigation kinds must reuse the evidence,
claim, audit, report, and workflow contracts instead of branching the runtime.

## Product contract

- Single-user pilot with owner-scoped data and a nullable future
  `organization_id`.
- Mandatory scope approval before collection and report approval before
  publication.
- Chinese and global public-web research plus uploaded documents.
- Every factual claim has evidence. Material claims require two independent
  registrable domains or remain `uncertain`.
- The service stores structured decisions and provenance, never hidden model
  reasoning.
- DeepSeek V4 Pro is used for planning/audit/synthesis and V4 Flash for
  collection/normalization when configured.

## Workflow

`draft -> planning -> awaiting_scope_approval -> collecting -> normalizing ->
analyzing -> auditing -> reworking -> synthesizing ->
awaiting_publish_approval -> published`.

Active stages may enter `cancelling -> cancelled`; unrecoverable failures enter
`failed`. Rework is capped at two rounds. The workflow is code-controlled;
agents submit validated domain objects through tools and cannot advance stages.

## Quality gates

- 2-5 approved competitors, six search angles per competitor.
- At most 30 accepted evidence records per competitor.
- 30-minute and 300K-token hard budgets; warning at 80%.
- Evidence credibility is the deterministic sum of source authority (30),
  freshness (20), extraction quality (10), specificity (10), and independent
  corroboration (30).
- Pricing requires an official source or an explicit third-party-estimate flag.
- Conflicting evidence is preserved as `contradicts`.
- Every Claim-Evidence relation contains a verbatim Snapshot substring,
  offsets, Snapshot SHA-256, validation status, and semantic entailment
  verdict. Numeric values absent from supporting quotes are rejected.
- Pricing Claims are generated only from structured PriceObservations whose
  amount, currency, billing period, and source quote pass exact validation.
- Analyze uses hybrid BM25 plus hashed n-gram retrieval over immutable chunks;
  Audit receives Claim-specific bindings instead of whole documents.

## Delivery

The dedicated workspace exposes investigation history, scope approval, live
stages, evidence, claims, audit issues, report review, and Web/Markdown/PDF
exports. PostgreSQL, Redis-backed streaming/run ownership, DB run events, and
S3-compatible snapshot storage are required before external production rollout.

## Implemented vertical slice

- Independent fail-fast CI migration chain and persistent `ci_stage_items`.
- Owner-scoped Investigation, Scope, Evidence, Claim, Event and Report APIs.
- Lease-fenced workflow recovery with durable task envelopes, submissions, and
  receipts.
- Planning/Audit/Synthesis through ordinary DeerFlow Runs; Collect/Analyze and
  targeted audit rework through Durable Subagent Batches.
- Bocha, Tavily, Jina and development-only DDGS provider adapters.
- Deterministic two-domain Claim gate, persisted audit issues, at most two
  targeted rework rounds, and validated 11-section structured reports.
- Dedicated workspace with scope/publish approval, rejection/rework, evidence
  explorer, Claim and audit-issue views, per-Agent item status, Markdown
  download and browser PDF printing.
- Production startup rejects missing search or Jina provider credentials.
- Token usage is recorded in an idempotent budget ledger; early stages reserve
  20% for Audit/Rework/Synthesis and all execution observes the deadline.
- Owner-scoped domain submission tools are mandatory for every Run/Batch
  Agent. Uploads, local/S3-compatible artifacts, and server-side Chromium PDF
  export share the same Evidence and report provenance.

Before external production rollout, configure the existing DeerFlow database,
stream bridge, run ownership and run-event settings for PostgreSQL/Redis/DB, and
provide an S3-compatible implementation for `snapshot_ref`; V1 development
keeps full extracted excerpts in SQL and leaves `snapshot_ref` optional.

See [Multi-Agent Orchestration](COMPETITIVE_RESEARCH_MULTI_AGENT_ORCHESTRATION.md)
for scheduling, communication, isolation, validation, retry, and recovery
semantics.

See [V1 完成说明](COMPETITIVE_RESEARCH_V1_COMPLETION_ZH.md) for the strict
evidence, pricing, budget, storage, deployment, and golden-evaluation contract.
