import { fetch } from "@/core/api/fetcher";

import type {
  Claim,
  Evidence,
  Investigation,
  Report,
  ResearchScope,
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

export async function getLatestReport(id: string): Promise<Report | null> {
  const response = await fetch(
    `/api/investigations/${encodeURIComponent(id)}/reports/latest`,
  );
  if (response.status === 404) return null;
  return json(response);
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
