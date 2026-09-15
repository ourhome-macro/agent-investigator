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
  token_used: number;
  token_budget: number;
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
  status: string;
}

export interface Claim {
  id: string;
  dimension: string;
  text: string;
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
  status: "supported" | "contradicted" | "uncertain";
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
}

export interface Report {
  id: string;
  version: number;
  status: string;
  rendered_markdown: string;
  created_at: string;
}

export interface StageItem {
  id: string;
  task_id: string;
  workflow_run_id: string;
  stage_attempt_id: string;
  stage: string;
  item_key: string;
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
