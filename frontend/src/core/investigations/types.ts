export type InvestigationStatus =
  | "draft"
  | "planning"
  | "awaiting_scope_approval"
  | "collecting"
  | "normalizing"
  | "analyzing"
  | "auditing"
  | "reworking"
  | "synthesizing"
  | "awaiting_publish_approval"
  | "published"
  | "cancelling"
  | "cancelled"
  | "failed";

export interface ResearchScope {
  market: string;
  audience: string;
  language: "zh-CN" | "en-US";
  time_range: string;
  competitors: string[];
  dimensions: string[];
  official_domains?: Record<string, string[]>;
  official_repositories?: Record<string, string[]>;
  required_dimensions?: string[];
  perspective?: ResearchPerspective;
  decision_goal?: string;
  version?: number;
  approved_at?: string | null;
}

export interface Investigation {
  id: string;
  investigation_type: "competitive_research";
  title: string;
  brief: string;
  status: InvestigationStatus;
  workflow_version: string;
  scope: ResearchScope;
  rework_round: number;
  failure_retry_count: number;
  token_used: number;
  token_reserved?: number;
  token_budget: number;
  research_mode?: ResearchMode;
  policy_snapshot?: ResearchPolicy;
  resource_policy?: ResearchPolicy;
  active_request_id?: string | null;
  deadline_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Evidence {
  id: string;
  snapshot_id: string | null;
  title: string;
  source_url: string;
  source_domain: string;
  excerpt: string;
  credibility_score: number;
  retrieved_at?: string;
  published_at?: string | null;
  status: string;
}

export interface Claim {
  id: string;
  dimension: string;
  text: string;
  version?: number;
  competitor_id?: string | null;
  statement?: {
    subject?: string;
    predicate?: string;
    object?: string;
    conditions?: string;
  };
  material: boolean;
  evidence_ids: string[];
  evidence_bindings: Array<{
    evidence_id: string;
    relation: "supports" | "contradicts" | "context";
    verbatim_quote: string;
    quote_start: number;
    quote_end: number;
    snapshot_sha256: string;
    validation_status: string;
    entailment_status: string | null;
  }>;
  status:
    | "supported"
    | "contradicted"
    | "uncertain"
    | "superseded"
    | "rejected";
  publication_eligible?: boolean;
  support_basis?:
    | "corroborated"
    | "official_documented"
    | "vendor_stated"
    | "user_reported"
    | "unverified"
    | "legacy_unverified";
  display_text?: string;
  independent_source_count: number;
}

export interface PriceObservation {
  id: string;
  claim_id: string;
  evidence_id: string;
  plan_name: string;
  amount: string;
  currency: string;
  billing_period: string;
  billing_unit: string;
  region: string | null;
  official: boolean;
  verbatim_quote: string;
}

export interface AuditIssue {
  id: string;
  claim_id: string | null;
  section_id: string | null;
  severity: "info" | "warning" | "error";
  rule: string;
  reason: string;
  required_action: string;
  status: "open" | "resolved" | "waived";
  blocking?: boolean;
}

export interface Report {
  id: string;
  version: number;
  status: string;
  rendered_markdown: string;
  created_at: string;
  structured_data?: {
    partial?: boolean;
    completion_status?: "incomplete" | "completed" | "completed_with_gaps";
    coverage_status?: "complete" | "has_gaps";
    sections?: ReportSection[];
    coverage?: CoverageCell[];
    claim_versions?: Record<string, number>;
    claim_snapshots?: Array<
      Pick<Claim, "id" | "text" | "display_text" | "version">
    >;
    decision?: {
      perspective: ResearchPerspective;
      label: string;
      goal: string;
      question: string;
    };
  };
}

export type ResearchMode = "quick" | "standard" | "deep";
export type ResearchPerspective =
  | "product"
  | "purchase"
  | "sales"
  | "operations";
export interface ResearchPolicy {
  mode: ResearchMode;
  label: string;
  description: string;
  base_tokens: number;
  extra_tokens: number;
  token_budget: number;
  minutes: number;
  search_results: number;
  max_rework_rounds: number;
  refinement_tokens: number;
  refinement_minutes: number;
  max_annotations: number;
}
export interface ResearchOptions {
  modes: Array<ResearchPolicy & { id: ResearchMode }>;
  perspectives: Array<{
    id: ResearchPerspective;
    label: string;
    question: string;
    section_title: string;
  }>;
}
export interface ReportSection {
  id: string;
  type: string;
  title: string;
  markdown: string;
  claim_ids: string[];
  evidence_ids: string[];
}
export interface EvidenceSnapshot {
  evidence_id: string;
  title: string;
  source_url: string;
  content: string;
  sha256: string;
  retrieved_at: string;
}
export interface AnnotationInput {
  report_version: number;
  section_key: string;
  selected_text: string;
  comment: string;
  action: "recollect" | "revise" | "investigate_conflict";
  claim_id?: string;
  claim_version?: number;
  competitor_id?: string;
  dimension?: string;
  idempotency_key: string;
}
export interface ResearchAnnotation {
  id: string;
  source_report_id: string;
  result_report_id: string | null;
  status:
    | "queued"
    | "running"
    | "completed"
    | "needs_review"
    | "failed"
    | "cancelled";
  payload: AnnotationInput;
  target: {
    claim_id: string | null;
    claim_version: number | null;
    competitor_id: string;
    dimension: string;
  };
  token_allowance: number;
  error?: string | null;
}

export interface CoverageCell {
  competitor_id: string;
  competitor: string;
  dimension: string;
  status: "covered" | "partial" | "missing";
  claim_ids: string[];
}

export interface StageItem {
  id: string;
  task_id: string;
  workflow_run_id: string;
  stage_attempt_id: string;
  stage: string;
  item_key: string;
  subject_label?: string;
  role: string;
  status:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "rejected"
    | "cancelled";
  attempt: number;
  max_attempts: number;
  durable_batch_id: string | null;
  durable_batch_item_id: string | null;
  error: string | null;
  receipt: {
    model_name?: string | null;
    token_usage?: Record<string, unknown> | null;
    warnings?: string[];
  } | null;
}
