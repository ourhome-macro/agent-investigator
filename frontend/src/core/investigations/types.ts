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
  created_at: string;
  updated_at: string;
}

export interface Evidence {
  id: string;
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
  status: "supported" | "contradicted" | "uncertain";
  independent_source_count: number;
}

export interface Report {
  id: string;
  version: number;
  status: string;
  rendered_markdown: string;
  created_at: string;
}
