"use client";

import { Download, ExternalLink, RefreshCw, Upload } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import {
  approveReport,
  approveScope,
  getInvestigation,
  getCoverage,
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
  type CoverageCell,
  type Evidence,
  type Investigation,
  type PriceObservation,
  type Report,
  type StageItem,
} from "@/core/investigations";
import {
  claimDisplayStatus,
  claimStatusExplanation,
  researchStageLabel,
  researchActivityLabel,
  researchNextStep,
  reportStatusLabel,
  executionLabel,
  bindingLabel,
  issueTitle,
  issueExplanation,
  issueAction,
  readableError,
  billingLabel,
  shouldPollInvestigation,
} from "@/core/investigations/quality";

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
  const [coverage, setCoverage] = useState<CoverageCell[]>([]);
  const loadSequence = useRef(0);
  const loadingId = useRef<string | null>(null);
  const load = useCallback(async () => {
    if (loadingId.current === id) return;
    loadingId.current = id;
    const sequence = ++loadSequence.current;
    try {
      const current = await getInvestigation(id);
      const [
        nextEvidence,
        nextClaims,
        nextAuditIssues,
        nextPricing,
        nextReport,
        nextStageItems,
        nextCoverage,
      ] = await Promise.all([
        listEvidence(id),
        listClaims(id),
        listAuditIssues(id),
        listPricing(id),
        getLatestReport(id),
        listStageItems(id),
        getCoverage(id),
      ]);
      if (sequence !== loadSequence.current) return;
      setInvestigation(current);
      setCoverage(nextCoverage);
      setEvidence(nextEvidence);
      setClaims(nextClaims);
      setAuditIssues(nextAuditIssues);
      setPricing(nextPricing);
      setReport(nextReport);
      setStageItems(nextStageItems);
      setError(null);
    } catch (reason) {
      if (sequence !== loadSequence.current) return;
      setError(reason instanceof Error ? reason.message : "加载失败");
    } finally {
      if (sequence === loadSequence.current) loadingId.current = null;
    }
  }, [id]);
  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      loadingId.current = null;
    };
  }, [load]);
  useEffect(() => {
    if (!shouldPollInvestigation(investigation?.status)) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load, investigation?.status]);
  if (!investigation)
    return (
      <main className="p-10">
        {error ? readableError(error) : "正在加载研究记录…"}
        {error && (
          <Button
            className="ml-3"
            variant="outline"
            onClick={() => void load()}
          >
            重新加载
          </Button>
        )}
      </main>
    );
  return (
    <main className="mx-auto w-full max-w-7xl space-y-8 p-6 md:p-10">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-muted-foreground text-sm">
            {researchStageLabel(investigation.status)}
          </p>
          <h1 className="text-3xl font-semibold">{investigation.title}</h1>
          <p className="text-muted-foreground mt-2 max-w-3xl">
            {investigation.brief}
          </p>
          <p className="mt-3 max-w-3xl text-sm">
            {researchNextStep(investigation.status)}
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
        <div role="alert" className="text-destructive rounded-lg border p-3">
          {readableError(error)}
          <details className="mt-2 text-xs">
            <summary>技术详情</summary>
            <pre className="whitespace-pre-wrap">{error}</pre>
          </details>
        </div>
      )}
      {investigation.status === "awaiting_scope_approval" && (
        <section className="border-primary/40 rounded-xl border p-6">
          <h2 className="text-xl font-medium">确认要研究什么</h2>
          <p className="mt-3">
            竞品：{investigation.scope.competitors.join("、")}
          </p>
          <p className="text-muted-foreground mt-2">
            比较项目：{investigation.scope.dimensions.join("、")}
          </p>
          <p className="text-muted-foreground mt-2 text-sm">
            必须回答的项目：
            {investigation.scope.required_dimensions?.length
              ? investigation.scope.required_dimensions.join("、")
              : "每个竞品至少形成一项有依据的产品事实；其他信息缺口会如实注明。"}
          </p>
          <div className="mt-3 space-y-1 text-sm">
            <p>请核对官方来源：这些资料可单独支持范围明确的产品说明。</p>
            {investigation.scope.competitors.map((name) => (
              <p key={name}>
                {name}：
                {[
                  ...(investigation.scope.official_domains?.[name] ?? []),
                  ...(investigation.scope.official_repositories?.[name] ?? []),
                ].join("、") || "尚未确认官方来源"}
              </p>
            ))}
          </div>
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
            <h2 className="text-xl font-medium">这次研究尚未完成</h2>
            <p className="text-muted-foreground mt-2 text-sm">
              已确定的研究范围和收集到的资料会保留。请先检查下面的中断原因；服务恢复且额度足够时，可以继续研究。
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
              尝试继续研究
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
              保存已有结果与信息缺口
            </Button>
          </section>
        )}
      <section className="grid gap-4 md:grid-cols-5">
        {[
          ["收集到的资料", evidence.length],
          [
            "提出的结论",
            claims.filter(
              (item) => !["superseded", "rejected"].includes(item.status),
            ).length,
          ],
          [
            "可引用的结论",
            claims.filter((item) => item.publication_eligible === true).length,
          ],
          [
            "仍需核实的结论",
            claims.filter(
              (item) =>
                item.publication_eligible !== true &&
                !["superseded", "rejected"].includes(item.status),
            ).length,
          ],
          [
            "分析额度已用",
            `${Math.round((investigation.token_used / Math.max(1, investigation.token_budget)) * 100)}%`,
          ],
        ].map(([label, value]) => (
          <div key={label} className="rounded-xl border p-4">
            <p className="text-muted-foreground text-sm">{label}</p>
            <p className="mt-2 text-2xl font-semibold">{value}</p>
          </div>
        ))}
      </section>
      <p className="text-muted-foreground text-sm">
        “可引用”不代表都是独立验证的事实：官方说明、厂商自述和用户反馈会分别标注。分析额度用于限制模型用量，不代表完成进度或实际费用。
      </p>
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-medium">执行进度</h2>
          <span className="text-muted-foreground text-sm">
            {stageItems.filter((item) => item.status === "succeeded").length}/
            {stageItems.length} 完成
          </span>
        </div>
        <div className="overflow-hidden rounded-xl border">
          {stageItems.length === 0 ? (
            <p className="text-muted-foreground p-5 text-sm">
              研究开始后，这里会显示收集资料、提炼结论和核对依据等步骤的进度。
            </p>
          ) : (
            stageItems.map((item, index) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center gap-3 border-b p-4 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">
                    {researchActivityLabel(item.stage)} ·{" "}
                    {item.subject_label?.trim()
                      ? item.subject_label
                      : `第 ${index + 1} 项`}
                  </p>
                  <p className="text-muted-foreground text-xs">
                    {item.attempt > 1
                      ? `第 ${item.attempt} 次处理`
                      : "首次处理"}
                  </p>
                  {item.error && (
                    <p className="text-destructive mt-1 text-xs">
                      {readableError(item.error)}
                    </p>
                  )}
                  <details className="text-muted-foreground mt-2 text-xs">
                    <summary>技术详情</summary>
                    <p>任务编号：{item.item_key}</p>
                    <p>执行角色：{item.role}</p>
                    <p>
                      模型用量：{investigation.token_used.toLocaleString()} /{" "}
                      {investigation.token_budget.toLocaleString()}{" "}
                      Token（本次研究总计）
                    </p>
                    {item.error && (
                      <pre className="whitespace-pre-wrap">{item.error}</pre>
                    )}
                  </details>
                </div>
                <span className="bg-muted rounded-full px-2 py-1 text-xs">
                  {executionLabel(item.status)}
                </span>
              </div>
            ))
          )}
        </div>
      </section>
      <section className="space-y-3">
        <h2 className="text-xl font-medium">哪些问题已有依据</h2>
        <p className="text-muted-foreground text-sm">
          “缺少资料”表示暂时没有足够信息，不代表产品没有这项能力。部分问题仍未知时，研究也可以完成并注明缺口。
        </p>
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="p-3">竞品</th>
                <th className="p-3">研究问题</th>
                <th className="p-3">状态</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((cell) => (
                <tr
                  className="border-t"
                  key={`${cell.competitor_id}:${cell.dimension}`}
                >
                  <td className="p-3">{cell.competitor}</td>
                  <td className="p-3">{cell.dimension}</td>
                  <td className="p-3">
                    {cell.status === "covered"
                      ? "已有产品事实依据"
                      : cell.status === "partial"
                        ? "有线索，仍需核实"
                        : "缺少资料"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h2 className="text-xl font-medium">研究结论与依据</h2>
        {claims.map((claim) => (
          <article key={claim.id} className="rounded-lg border p-4">
            <div className="flex justify-between gap-3">
              <span className="text-sm font-medium">{claim.dimension}</span>
              <span
                className={
                  claim.publication_eligible === true &&
                  !["vendor_stated", "user_reported"].includes(
                    claim.support_basis ?? "",
                  )
                    ? "text-emerald-600"
                    : "text-amber-600"
                }
              >
                {claimDisplayStatus(claim)}
              </span>
            </div>
            <p className="mt-2">{claim.display_text ?? claim.text}</p>
            <p className="text-muted-foreground mt-2 text-sm">
              {claimStatusExplanation(claim)}
            </p>
            <p className="text-muted-foreground mt-2 text-xs">
              来自 {claim.independent_source_count} 个来源域名，关联{" "}
              {claim.evidence_ids.length} 份资料。来源数量本身不等于可信度。
            </p>
            <div className="mt-3 space-y-2">
              {claim.evidence_bindings.map((binding) => (
                <blockquote
                  key={`${binding.evidence_id}:${binding.relation}`}
                  className="border-l-2 pl-3 text-sm"
                >
                  <p>“{binding.verbatim_quote}”</p>
                  <p className="text-muted-foreground mt-1 text-xs">
                    {bindingLabel(binding.relation)} ·{" "}
                    {bindingLabel(binding.validation_status)} ·{" "}
                    {bindingLabel(binding.entailment_status)}
                  </p>
                  {evidence.find((item) => item.id === binding.evidence_id) && (
                    <a
                      className="text-primary mt-1 inline-block text-xs"
                      href={
                        evidence.find((item) => item.id === binding.evidence_id)
                          ?.source_url
                      }
                      target="_blank"
                      rel="noreferrer"
                    >
                      查看原文 ↗
                    </a>
                  )}
                </blockquote>
              ))}
            </div>
          </article>
        ))}
      </section>
      {auditIssues.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xl font-medium">待处理问题与补充说明</h2>
          {auditIssues.map((issue) => (
            <article
              key={issue.id}
              className="rounded-lg border border-amber-300 p-4"
            >
              <div className="flex flex-wrap justify-between gap-2">
                <span className="font-medium">{issueTitle(issue)}</span>
                <span className="text-sm text-amber-700">
                  {issue.status === "resolved"
                    ? "已处理"
                    : issue.status === "waived"
                      ? "已备注"
                      : issue.blocking
                        ? "影响结论，需先处理"
                        : "补充说明，不阻止其他结论使用"}
                </span>
              </div>
              <p className="mt-2 text-sm">{issueExplanation(issue)}</p>
              <p className="text-muted-foreground mt-2 text-xs">
                下一步：{issueAction(issue)}
              </p>
              <details className="text-muted-foreground mt-2 text-xs">
                <summary>技术详情</summary>
                <p>
                  {issue.rule} · {issue.severity} · {issue.status}
                </p>
                <p>{issue.reason}</p>
                <p>{issue.required_action}</p>
              </details>
            </article>
          ))}
        </section>
      )}
      {pricing.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xl font-medium">价格与收费方式</h2>
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
                    <td className="p-3">{billingLabel(item.billing_period)}</td>
                    <td className="p-3">{billingLabel(item.billing_unit)}</td>
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
        <h2 className="text-xl font-medium">收集到的资料</h2>
        <p className="text-muted-foreground text-sm">
          可以打开原文核对内容。资料被收集，并不代表它能支持所有相关结论。
        </p>
        <div className="grid gap-3 md:grid-cols-2">
          {evidence.map((item) => (
            <article key={item.id} className="rounded-lg border p-4">
              <div className="flex justify-between gap-3">
                <h3 className="font-medium">{item.title}</h3>
              </div>
              <p className="text-muted-foreground mt-2 line-clamp-4 text-sm">
                {item.excerpt}
              </p>
              <p className="text-muted-foreground mt-2 text-xs">
                {item.published_at
                  ? `资料日期：${new Date(item.published_at).toLocaleDateString("zh-CN")}`
                  : "资料未注明发布日期"}
                {item.retrieved_at
                  ? ` · 收集于 ${new Date(item.retrieved_at).toLocaleDateString("zh-CN")}`
                  : ""}
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
              <details className="text-muted-foreground mt-2 text-xs">
                <summary>资料评估详情</summary>来源筛选评分：
                {item.credibility_score}/100，仅用于辅助筛选，不是结论正确概率。
              </details>
            </article>
          ))}
        </div>
      </section>
      {report && (
        <section className="rounded-xl border p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-xl font-medium">
              {reportStatusLabel(report)} · 第 {report.version} 版
            </h2>
            <div className="flex gap-2">
              <Button variant="outline" asChild>
                <a href={`/api/investigations/${id}/exports/markdown`}>
                  <Download className="size-4" />
                  下载文本报告
                </a>
              </Button>
              <Button variant="outline" asChild>
                <a href={`/api/investigations/${id}/exports/pdf`}>
                  <Download className="size-4" />
                  下载 PDF
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
                    退回补充研究
                  </Button>
                  <Button
                    onClick={() =>
                      void approveReport(id, report.version)
                        .then(() => load())
                        .catch((reason: Error) => setError(reason.message))
                    }
                  >
                    {report.structured_data?.partial
                      ? "确认发布部分结果"
                      : "批准发布"}
                  </Button>
                </>
              )}
            </div>
          </div>
          <MarkdownContent
            className="mt-5"
            content={report.rendered_markdown}
            isLoading={false}
          />
        </section>
      )}
    </main>
  );
}
