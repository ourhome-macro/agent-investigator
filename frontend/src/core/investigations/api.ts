import { fetch } from "@/core/api/fetcher";

import type {
  AuditIssue,
  Claim,
  CoverageCell,
  Evidence,
  Investigation,
  PriceObservation,
  Report,
  ResearchScope,
  StageItem,
  ResearchMode,
  ResearchOptions,
  EvidenceSnapshot,
  AnnotationInput,
  ResearchAnnotation,
} from "./types";

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new Error(
      typeof payload?.detail === "string"
        ? payload.detail
        : `Investigation request failed (${response.status})`,
    );
  }
  return (await response.json()) as T;
}

export async function listInvestigations(): Promise<Investigation[]> {
  return json(await fetch("/api/investigations"));
}

export async function createInvestigation(input: {
  title: string;
  brief: string;
  scope: ResearchScope;
  mode?: ResearchMode;
}): Promise<Investigation> {
  return json(
    await fetch("/api/investigations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        investigation_type: "competitive_research",
        ...input,
      }),
    }),
  );
}

export async function getResearchOptions(): Promise<ResearchOptions> {
  return json(await fetch("/api/investigations/options"));
}

export async function getEvidenceSnapshot(
  id: string,
  evidenceId: string,
  signal?: AbortSignal,
): Promise<EvidenceSnapshot> {
  return json(
    await fetch(
      `/api/investigations/${encodeURIComponent(id)}/evidence/${encodeURIComponent(evidenceId)}/snapshot`,
      { signal },
    ),
  );
}

export async function listAnnotations(
  id: string,
): Promise<ResearchAnnotation[]> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/annotations`),
  );
}

export async function submitAnnotation(
  id: string,
  body: AnnotationInput,
): Promise<ResearchAnnotation> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/annotations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function getInvestigation(id: string): Promise<Investigation> {
  return json(await fetch(`/api/investigations/${encodeURIComponent(id)}`));
}

export async function approveScope(id: string): Promise<Investigation> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/scope/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
    }),
  );
}

export async function cancelInvestigation(id: string): Promise<Investigation> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
    }),
  );
}

export async function retryInvestigation(id: string): Promise<Investigation> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
    }),
  );
}

export async function listEvidence(id: string): Promise<Evidence[]> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/evidence`),
  );
}

export async function listClaims(id: string): Promise<Claim[]> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/claims`),
  );
}

export async function getCoverage(id: string): Promise<CoverageCell[]> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/coverage`),
  );
}

export async function listAuditIssues(id: string): Promise<AuditIssue[]> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/audit/issues`),
  );
}

export async function listPricing(id: string): Promise<PriceObservation[]> {
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/pricing`),
  );
}

export async function uploadMaterial(
  id: string,
  file: File,
): Promise<Evidence> {
  const body = new FormData();
  body.append("file", file);
  return json(
    await fetch(`/api/investigations/${encodeURIComponent(id)}/materials`, {
      method: "POST",
      body,
    }),
  );
}

export async function getLatestReport(id: string): Promise<Report | null> {
  const response = await fetch(
    `/api/investigations/${encodeURIComponent(id)}/reports/latest`,
  );
  if (response.status === 404) return null;
  return json(response);
}

export async function finalizePartialReport(id: string): Promise<Report> {
  return json(
    await fetch(
      `/api/investigations/${encodeURIComponent(id)}/reports/finalize-partial`,
      { method: "POST" },
    ),
  );
}

export async function listStageItems(id: string): Promise<StageItem[]> {
  return json(
    await fetch(
      `/api/investigations/${encodeURIComponent(id)}/orchestration/items`,
    ),
  );
}

export async function approveReport(
  id: string,
  version: number,
): Promise<Report> {
  return json(
    await fetch(
      `/api/investigations/${encodeURIComponent(id)}/reports/${version}/approve`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idempotency_key: crypto.randomUUID() }),
      },
    ),
  );
}

export async function rejectReport(
  id: string,
  version: number,
  reason: string,
): Promise<Investigation> {
  return json(
    await fetch(
      `/api/investigations/${encodeURIComponent(id)}/reports/${version}/reject`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, idempotency_key: crypto.randomUUID() }),
      },
    ),
  );
}
