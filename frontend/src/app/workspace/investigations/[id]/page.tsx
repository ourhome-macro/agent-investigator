"use client";

import { Download, ExternalLink, RefreshCw, Upload } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  approveReport,
  approveScope,
  getInvestigation,
  getLatestReport,
  finalizePartialReport,
  listAuditIssues,
  listClaims,
  listEvidence,
  listPricing,
  listStageItems,
  rejectReport,
  retryInvestigation,
  uploadMaterial,
  type AuditIssue,
  type Claim,
  type Evidence,
  type Investigation,
  type PriceObservation,
  type Report,
  type StageItem,
} from "@/core/investigations";

export default function InvestigationPage() {
  const id = String(useParams<{ id: string }>().id);
  const [investigation, setInvestigation] = useState<Investigation | null>(
    null,
  );
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [auditIssues, setAuditIssues] = useState<AuditIssue[]>([]);
  const [pricing, setPricing] = useState<PriceObservation[]>([]);
  const [report, setReport] = useState<Report | null>(null);
  const [stageItems, setStageItems] = useState<StageItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const current = await getInvestigation(id);
      setInvestigation(current);
      const [
        nextEvidence,
        nextClaims,
        nextAuditIssues,
        nextPricing,
        nextReport,
        nextStageItems,
      ] = await Promise.all([
        listEvidence(id),
        listClaims(id),
        listAuditIssues(id),
        listPricing(id),
        getLatestReport(id),
        listStageItems(id),
      ]);
      setEvidence(nextEvidence);
      setClaims(nextClaims);
      setAuditIssues(nextAuditIssues);
      setPricing(nextPricing);
      setReport(nextReport);
      setStageItems(nextStageItems);
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
        <div className="flex gap-2">
          {[
            "planning",
            "awaiting_scope_approval",
            "collecting",
            "reworking",
          ].includes(investigation.status) && (
            <Button variant="outline" asChild>
              <label>
                <Upload className="size-4" />
                上传材料
                <input
                  className="hidden"
                  type="file"
                  accept=".txt,.md,.csv,.json,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file)
                      void uploadMaterial(id, file)
                        .then(() => load())
                        .catch((reason: Error) => setError(reason.message));
                    event.target.value = "";
                  }}
                />
              </label>
            </Button>
          )}
          <Button variant="outline" onClick={() => void load()}>
            <RefreshCw className="size-4" />
            刷新
          </Button>
        </div>
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
      {investigation.status === "failed" &&
        investigation.failure_retry_count < 2 && (
          <section className="border-destructive/40 rounded-xl border p-6">
            <h2 className="text-xl font-medium">执行失败</h2>
            <p className="text-muted-foreground mt-2 text-sm">
              修复 Provider、预算或 Agent 配置后，可以保留当前 Scope 和 Evidence
              启动新的恢复轮次。
            </p>
            <Button
              className="mt-4"
              variant="destructive"
              onClick={() =>
                void retryInvestigation(id)
                  .then(setInvestigation)
                  .catch((reason: Error) => setError(reason.message))
              }
            >
              重试失败任务
            </Button>
            <Button
              className="mt-4 ml-2"
              variant="outline"
              onClick={() =>
                void finalizePartialReport(id)
                  .then((nextReport) => {
                    setReport(nextReport);
                    return load();
                  })
                  .catch((reason: Error) => setError(reason.message))
              }
            >
              生成不确定性报告
            </Button>
          </section>
        )}
      <section className="grid gap-4 md:grid-cols-5">
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
          [
            "Token 预算",
            `${investigation.token_used.toLocaleString()} / ${investigation.token_budget.toLocaleString()}`,
          ],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border p-4">
            <p className="text-muted-foreground text-sm">{label}</p>
            <p className="mt-2 text-2xl font-semibold">{value}</p>
          </div>
        ))}
      </section>
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-medium">Agent / Batch Items</h2>
          <span className="text-muted-foreground text-sm">
            {stageItems.filter((item) => item.status === "succeeded").length}/
            {stageItems.length} 完成
          </span>
        </div>
        <div className="overflow-hidden rounded-xl border">
          {stageItems.length === 0 ? (
            <p className="text-muted-foreground p-5 text-sm">
              创建调研后将显示每个 Agent / Batch Item 的执行与自动重试状态。
            </p>
          ) : (
            stageItems.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center gap-3 border-b p-4 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {item.item_key}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    {item.stage} · {item.role} · attempt {item.attempt}/
                    {item.max_attempts}
                  </p>
                  {item.error && (
                    <p className="text-destructive mt-1 text-xs">
                      {item.error}
                    </p>
                  )}
                </div>
                <span className="bg-muted rounded-full px-2 py-1 text-xs">
                  {item.status}
                </span>
              </div>
            ))
          )}
        </div>
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
            <div className="mt-3 space-y-2">
              {claim.evidence_bindings.map((binding) => (
                <blockquote
                  key={`${binding.evidence_id}:${binding.relation}`}
                  className="border-l-2 pl-3 text-sm"
                >
                  <p>“{binding.verbatim_quote}”</p>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {binding.relation} · {binding.validation_status} ·{" "}
                    {binding.entailment_status ?? "pending audit"}
                  </p>
                </blockquote>
              ))}
            </div>
          </article>
        ))}
      </section>
      {auditIssues.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xl font-medium">Audit Issues</h2>
          {auditIssues.map((issue) => (
            <article
              key={issue.id}
              className="rounded-lg border border-amber-300 p-4"
            >
              <div className="flex flex-wrap justify-between gap-2">
                <span className="font-medium">{issue.rule}</span>
                <span className="text-sm text-amber-700">
                  {issue.severity} · {issue.status}
                </span>
              </div>
              <p className="mt-2 text-sm">{issue.reason}</p>
              <p className="text-muted-foreground mt-2 text-xs">
                下一步：{issue.required_action}
              </p>
            </article>
          ))}
        </section>
      )}
      {pricing.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xl font-medium">结构化定价</h2>
          <div className="overflow-x-auto rounded-xl border">
            <table className="w-full text-left text-sm">
              <thead className="bg-muted">
                <tr>
                  <th className="p-3">套餐</th>
                  <th className="p-3">价格</th>
                  <th className="p-3">周期</th>
                  <th className="p-3">单位</th>
                  <th className="p-3">来源</th>
                </tr>
              </thead>
              <tbody>
                {pricing.map((item) => (
                  <tr key={item.id} className="border-t">
                    <td className="p-3">{item.plan_name}</td>
                    <td className="p-3">
                      {item.currency} {item.amount}
                    </td>
                    <td className="p-3">{item.billing_period}</td>
                    <td className="p-3">{item.billing_unit}</td>
                    <td className="p-3">
                      {item.official ? "官方" : "第三方估计"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
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
              <Button variant="outline" asChild>
                <a href={`/api/investigations/${id}/exports/pdf`}>
                  <Download className="size-4" />
                  PDF
                </a>
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
