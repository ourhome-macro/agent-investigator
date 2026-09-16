from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now_utc() -> datetime:
    return datetime.now(UTC)


class InvestigationBase(DeclarativeBase):
    pass


class InvestigationRow(InvestigationBase):
    __tablename__ = "ci_investigations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    project_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    investigation_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200))
    brief: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(48), index=True)
    workflow_version: Mapped[str] = mapped_column(String(32), default="competitive-research-v1")
    rework_round: Mapped[int] = mapped_column(Integer, default=0)
    failure_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    token_used: Mapped[int] = mapped_column(Integer, default=0)
    token_reserved: Mapped[int] = mapped_column(Integer, default=0)
    token_budget: Mapped[int] = mapped_column(Integer, default=300_000)
    research_mode: Mapped[str] = mapped_column(String(24), default="standard")
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    active_request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class ScopeRow(InvestigationBase):
    __tablename__ = "ci_scopes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    market: Mapped[str] = mapped_column(String(128))
    audience: Mapped[str] = mapped_column(String(256))
    language: Mapped[str] = mapped_column(String(16))
    time_range: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[list] = mapped_column(JSON)
    required_dimensions: Mapped[list] = mapped_column(JSON, default=list)
    decision_context: Mapped[dict] = mapped_column(JSON, default=dict)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)


class CompetitorRow(InvestigationBase):
    __tablename__ = "ci_competitors"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    canonical_name: Mapped[str] = mapped_column(String(200))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    official_domains: Mapped[list] = mapped_column(JSON, default=list)
    official_repositories: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("investigation_id", "canonical_name", name="uq_ci_competitor_name"),)


class WorkflowRunRow(InvestigationBase):
    __tablename__ = "ci_workflow_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StageAttemptRow(InvestigationBase):
    __tablename__ = "ci_stage_attempts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("ci_workflow_runs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(48), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("workflow_run_id", "stage", "attempt", name="uq_ci_stage_attempt"),)


class StageItemRow(InvestigationBase):
    __tablename__ = "ci_stage_items"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), index=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("ci_workflow_runs.id", ondelete="CASCADE"), index=True)
    stage_attempt_id: Mapped[str] = mapped_column(ForeignKey("ci_stage_attempts.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(48), index=True)
    item_key: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    task_envelope: Mapped[dict] = mapped_column(JSON)
    submission: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    receipt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    durable_batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    durable_batch_item_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    __table_args__ = (
        UniqueConstraint("stage_attempt_id", "item_key", name="uq_ci_stage_item_key"),
        Index("ix_ci_stage_item_workflow_stage", "workflow_run_id", "stage", "status"),
    )


class EvidenceRow(InvestigationBase):
    __tablename__ = "ci_evidence"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("ci_evidence_snapshots.id", ondelete="RESTRICT"), nullable=True, index=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    competitor_id: Mapped[str | None] = mapped_column(ForeignKey("ci_competitors.id", ondelete="SET NULL"), nullable=True, index=True)
    source_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    source_domain: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    excerpt: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(16))
    credibility_score: Mapped[int] = mapped_column(Integer)
    score_components: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), index=True)
    created_by_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("investigation_id", "content_hash", name="uq_ci_evidence_content"), Index("ix_ci_evidence_investigation_domain", "investigation_id", "source_domain"))


class ClaimRow(InvestigationBase):
    __tablename__ = "ci_claims"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(180), nullable=True, unique=True, index=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    competitor_id: Mapped[str | None] = mapped_column(ForeignKey("ci_competitors.id", ondelete="SET NULL"), nullable=True)
    dimension: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    material: Mapped[bool] = mapped_column(Boolean, default=True)
    claim_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), index=True)
    independent_source_count: Mapped[int] = mapped_column(Integer, default=0)
    support_basis: Mapped[str] = mapped_column(String(32), default="unverified")
    statement: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by_agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ClaimEvidenceRow(InvestigationBase):
    __tablename__ = "ci_claim_evidence"
    claim_id: Mapped[str] = mapped_column(ForeignKey("ci_claims.id", ondelete="CASCADE"), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("ci_evidence.id", ondelete="CASCADE"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(16), primary_key=True)
    support_score: Mapped[int] = mapped_column(Integer, default=0)
    quoted_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    quote_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    validation_status: Mapped[str] = mapped_column(String(24), default="pending")
    entailment_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    auditor_status: Mapped[str | None] = mapped_column(String(24), nullable=True)


class EvidenceSnapshotRow(InvestigationBase):
    __tablename__ = "ci_evidence_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    content_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    extraction_method: Mapped[str] = mapped_column(String(64), index=True)
    language: Mapped[str] = mapped_column(String(16))
    object_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("investigation_id", "content_hash", name="uq_ci_snapshot_content"),)


class EvidenceChunkRow(InvestigationBase):
    __tablename__ = "ci_evidence_chunks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("ci_evidence_snapshots.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    token_estimate: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("snapshot_id", "ordinal", name="uq_ci_snapshot_chunk_ordinal"),)


class PriceObservationRow(InvestigationBase):
    __tablename__ = "ci_price_observations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("ci_claims.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("ci_evidence.id", ondelete="CASCADE"), index=True)
    plan_name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(8), index=True)
    billing_period: Mapped[str] = mapped_column(String(16), index=True)
    billing_unit: Mapped[str] = mapped_column(String(128))
    seat_minimum: Mapped[int | None] = mapped_column(Integer, nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tax_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    promotion: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    official: Mapped[bool] = mapped_column(Boolean)
    verbatim_quote: Mapped[str] = mapped_column(Text)
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditIssueRow(InvestigationBase):
    __tablename__ = "ci_audit_issues"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("ci_claims.id", ondelete="CASCADE"), nullable=True)
    section_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    rule: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    required_action: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    raised_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ReportRow(InvestigationBase):
    __tablename__ = "ci_reports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    schema_version: Mapped[str] = mapped_column(String(32), default="1")
    structured_data: Mapped[dict] = mapped_column(JSON)
    rendered_markdown: Mapped[str] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    __table_args__ = (UniqueConstraint("investigation_id", "version", name="uq_ci_report_version"),)


class ReportSectionRow(InvestigationBase):
    __tablename__ = "ci_report_sections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("ci_reports.id", ondelete="CASCADE"), index=True)
    section_type: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    structured_data: Mapped[dict] = mapped_column(JSON)
    rendered_markdown: Mapped[str] = mapped_column(Text)
    claim_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)


class ReportAnnotationRow(InvestigationBase):
    __tablename__ = "ci_report_annotations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("ci_reports.id", ondelete="CASCADE"), index=True)
    section_id: Mapped[str | None] = mapped_column(ForeignKey("ci_report_sections.id", ondelete="CASCADE"), nullable=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class InvestigationEventRow(InvestigationBase):
    __tablename__ = "ci_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    stage: Mapped[str | None] = mapped_column(String(48), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ExportRow(InvestigationBase):
    __tablename__ = "ci_exports"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("ci_reports.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24))
    object_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class BudgetEntryRow(InvestigationBase):
    __tablename__ = "ci_budget_entries"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(ForeignKey("ci_workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(48), index=True)
    task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    durable_batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class BudgetReservationRow(InvestigationBase):
    __tablename__ = "ci_budget_reservations"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(48))
    tokens: Mapped[int] = mapped_column(Integer)
    actual_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResearchCandidateRow(InvestigationBase):
    __tablename__ = "ci_candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)


class ClaimSubmissionRow(InvestigationBase):
    __tablename__ = "ci_claim_submissions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claim_id: Mapped[str] = mapped_column(ForeignKey("ci_claims.id", ondelete="CASCADE"), index=True)


class ResearchRequestRow(InvestigationBase):
    __tablename__ = "ci_research_requests"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("ci_investigations.id", ondelete="CASCADE"), index=True)
    source_report_id: Mapped[str] = mapped_column(ForeignKey("ci_reports.id"))
    result_report_id: Mapped[str | None] = mapped_column(ForeignKey("ci_reports.id"), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    target: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_allowance: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
