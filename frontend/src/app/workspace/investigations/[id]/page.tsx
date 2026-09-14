"use client";

import { Download, ExternalLink, Printer, RefreshCw } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  approveReport,
  approveScope,
  getInvestigation,
  getLatestReport,
  listClaims,
  listEvidence,
  rejectReport,
  type Claim,
  type Evidence,
  type Investigation,
  type Report,
} from "@/core/investigations";

export default function InvestigationPage() {
  const id = String(useParams<{ id: string }>().id);
  const [investigation, setInvestigation] = useState<Investigation | null>(
    null,
  );
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const current = await getInvestigation(id);
      setInvestigation(current);
      const [nextEvidence, nextClaims, nextReport] = await Promise.all([
        listEvidence(id),
        listClaims(id),
        getLatestReport(id),
      ]);
      setEvidence(nextEvidence);
      setClaims(nextClaims);
      setReport(nextReport);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "加载失败");
    }
  }, [id]);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);
  if (!investigation) return <main className="p-10">{error ?? "加载中…"}</main>;
  return (
    <main className="mx-auto w-full max-w-7xl space-y-8 p-6 md:p-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-muted-foreground text-sm">
            {investigation.status}
          </p>
          <h1 className="text-3xl font-semibold">{investigation.title}</h1>
          <p className="text-muted-foreground mt-2 max-w-3xl">
            {investigation.brief}
          </p>
        </div>
        <Button variant="outline" onClick={() => void load()}>
          <RefreshCw className="size-4" />
          刷新
        </Button>
      </header>
      {error && (
        <div className="text-destructive rounded-lg border p-3">{error}</div>
      )}
      {investigation.status === "awaiting_scope_approval" && (
        <section className="border-primary/40 rounded-xl border p-6">
          <h2 className="text-xl font-medium">开题确认</h2>
          <p className="mt-3">
            竞品：{investigation.scope.competitors.join("、")}
          </p>
          <p className="text-muted-foreground mt-2">
            维度：{investigation.scope.dimensions.join("、")}
          </p>
          <Button
            className="mt-5"
            onClick={() =>
              void approveScope(id)
                .then(setInvestigation)
                .catch((reason: Error) => setError(reason.message))
            }
          >
            批准并开始研究
          </Button>
        </section>
      )}
      <section className="grid gap-4 md:grid-cols-4">
        {[
          ["证据", evidence.length],
          ["结论", claims.length],
          [
            "已验证",
            claims.filter((item) => item.status === "supported").length,
          ],
          [
            "证据不足",
            claims.filter((item) => item.status === "uncertain").length,
          ],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border p-4">
            <p className="text-muted-foreground text-sm">{label}</p>
            <p className="mt-2 text-2xl font-semibold">{value}</p>
          </div>
        ))}
      </section>
      <section className="space-y-3">
        <h2 className="text-xl font-medium">Claim 审计</h2>
        {claims.map((claim) => (
          <article key={claim.id} className="rounded-lg border p-4">
            <div className="flex justify-between gap-3">
              <span className="text-sm font-medium">{claim.dimension}</span>
              <span
                className={
                  claim.status === "supported"
                    ? "text-emerald-600"
                    : "text-amber-600"
                }
              >
                {claim.status}
              </span>
            </div>
            <p className="mt-2">{claim.text}</p>
            <p className="text-muted-foreground mt-2 text-xs">
              独立来源 {claim.independent_source_count} · Evidence{" "}
              {claim.evidence_ids.length}
            </p>
          </article>
        ))}
      </section>
      <section className="space-y-3">
        <h2 className="text-xl font-medium">Evidence Explorer</h2>
        <div className="grid gap-3 md:grid-cols-2">
          {evidence.map((item) => (
            <article key={item.id} className="rounded-lg border p-4">
              <div className="flex justify-between gap-3">
                <h3 className="font-medium">{item.title}</h3>
                <span>{item.credibility_score}/100</span>
              </div>
              <p className="text-muted-foreground mt-2 line-clamp-4 text-sm">
                {item.excerpt}
              </p>
              <a
                className="text-primary mt-3 inline-flex items-center gap-1 text-sm"
                href={item.source_url}
                target="_blank"
                rel="noreferrer"
              >
                {item.source_domain}
                <ExternalLink className="size-3" />
              </a>
            </article>
          ))}
        </div>
      </section>
      {report && (
        <section className="rounded-xl border p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-xl font-medium">报告 v{report.version}</h2>
            <div className="flex gap-2">
              <Button variant="outline" asChild>
                <a href={`/api/investigations/${id}/exports/markdown`}>
                  <Download className="size-4" />
                  Markdown
                </a>
              </Button>
              <Button variant="outline" onClick={() => window.print()}>
                <Printer className="size-4" />
                打印 / PDF
              </Button>
              {investigation.status === "awaiting_publish_approval" && (
                <>
                  <Button
                    variant="destructive"
                    onClick={() => {
                      const reason = window.prompt("请输入返工原因");
                      if (reason)
                        void rejectReport(id, report.version, reason)
                          .then(() => load())
                          .catch((error: Error) => setError(error.message));
                    }}
                  >
                    退回返工
                  </Button>
                  <Button
                    onClick={() =>
                      void approveReport(id, report.version)
                        .then(() => load())
                        .catch((reason: Error) => setError(reason.message))
                    }
                  >
                    批准发布
                  </Button>
                </>
              )}
            </div>
          </div>
          <pre className="bg-muted mt-5 max-h-[42rem] overflow-auto rounded-lg p-5 text-sm whitespace-pre-wrap print:max-h-none print:border-0 print:bg-white print:p-0">
            {report.rendered_markdown}
          </pre>
        </section>
      )}
    </main>
  );
}
