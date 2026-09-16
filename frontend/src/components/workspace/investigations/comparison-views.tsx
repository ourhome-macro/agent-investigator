"use client";

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type Claim,
  type CoverageCell,
  type Evidence,
  type Investigation,
  type PriceObservation,
} from "@/core/investigations";
import {
  billingLabel,
  claimDisplayStatus,
} from "@/core/investigations/quality";

import { type SourceSelection } from "./source-dialog";

export function ComparisonViews({
  investigation,
  claims,
  coverage,
  pricing,
  evidence,
  onSource,
  onRefine,
  canRefine,
}: {
  investigation: Investigation;
  claims: Claim[];
  coverage: CoverageCell[];
  pricing: PriceObservation[];
  evidence: Evidence[];
  onSource: (selection: SourceSelection) => void;
  onRefine: (target: Claim | CoverageCell) => void;
  canRefine: boolean;
}) {
  const [view, setView] = useState("comparison");
  const [query, setQuery] = useState("");
  const [competitor, setCompetitor] = useState("");
  const [dimension, setDimension] = useState("");
  const brands = useMemo(
    () =>
      Array.from(
        new Map(
          coverage.map((cell) => [cell.competitor_id, cell.competitor]),
        ).entries(),
      ).map(([id, name]) => ({ id, name })),
    [coverage],
  );
  const shownBrands = brands.filter(
    (brand) => !competitor || brand.id === competitor,
  );
  const eligible = claims.filter(
    (claim) =>
      claim.publication_eligible &&
      (!competitor || claim.competitor_id === competitor),
  );
  const visiblePrices = pricing.filter((price) => {
    const claim = eligible.find((item) => item.id === price.claim_id);
    return (
      claim &&
      (!dimension || claim.dimension === dimension) &&
      (!query ||
        `${price.plan_name} ${price.amount} ${price.currency}`
          .toLowerCase()
          .includes(query.toLowerCase()))
    );
  });
  const dimensions = investigation.scope.dimensions.filter(
    (item) =>
      (!dimension || item === dimension) &&
      (!query ||
        item.toLowerCase().includes(query.toLowerCase()) ||
        eligible.some(
          (claim) =>
            claim.dimension === item &&
            claim.text.toLowerCase().includes(query.toLowerCase()),
        )),
  );
  const sourceButtons = (claim: Claim) => (
    <div className="mt-2 flex flex-wrap gap-2">
      {claim.evidence_bindings.map((binding, index) => (
        <button
          key={binding.evidence_id}
          type="button"
          className="text-primary text-xs underline"
          onClick={() => onSource({ evidenceId: binding.evidence_id, binding })}
        >
          原文 {index + 1}
        </button>
      ))}
      {canRefine && (
        <button
          type="button"
          className="text-primary text-xs underline"
          onClick={() => onRefine(claim)}
        >
          对此结论补研
        </button>
      )}
    </div>
  );
  return (
    <section className="space-y-4 rounded-xl border p-5">
      <div>
        <h2 className="text-xl font-medium">产品对比与选择依据</h2>
        <p className="text-muted-foreground mt-1 text-sm">
          并排查看已核对的发现。未知项保持未知，来源说法与产品事实分开标注。
        </p>
      </div>
      <div
        role="tablist"
        aria-label="对比视图"
        className="flex flex-wrap gap-2"
      >
        {[
          ["comparison", "功能与条件"],
          ["pricing", "价格与方案"],
          ["audience", "用户与场景"],
        ].map(([key, label]) => (
          <Button
            key={key}
            role="tab"
            aria-selected={view === key}
            variant={view === key ? "default" : "outline"}
            onClick={() => {
              setView(key!);
              setDimension("");
              setQuery("");
            }}
          >
            {label}
          </Button>
        ))}
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <Input
          aria-label="搜索对比结论"
          placeholder="搜索功能、限制或关键词"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          aria-label="筛选竞品"
          className="rounded-md border p-2 text-sm"
          value={competitor}
          onChange={(event) => setCompetitor(event.target.value)}
        >
          <option value="">全部竞品</option>
          {brands.map((brand) => (
            <option key={brand.id} value={brand.id}>
              {brand.name}
            </option>
          ))}
        </select>
        <select
          aria-label="筛选比较项目"
          className="rounded-md border p-2 text-sm"
          value={dimension}
          onChange={(event) => setDimension(event.target.value)}
        >
          <option value="">全部比较项目</option>
          {investigation.scope.dimensions.map((item) => (
            <option key={item} value={item}>
              {item}
            </option>
          ))}
        </select>
      </div>
      {view === "comparison" && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[36rem] text-left text-sm">
            <thead>
              <tr>
                <th className="p-3">比较项目</th>
                {shownBrands.map((brand) => (
                  <th key={brand.id} className="p-3">
                    {brand.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {dimensions.map((item) => (
                <tr className="border-t align-top" key={item}>
                  <th className="p-3">{item}</th>
                  {shownBrands.map((brand) => {
                    const findings = eligible.filter(
                      (claim) =>
                        claim.competitor_id === brand.id &&
                        claim.dimension === item &&
                        (!query ||
                          item.toLowerCase().includes(query.toLowerCase()) ||
                          claim.text
                            .toLowerCase()
                            .includes(query.toLowerCase())),
                    );
                    const cell = coverage.find(
                      (entry) =>
                        entry.competitor_id === brand.id &&
                        entry.dimension === item,
                    );
                    return (
                      <td className="min-w-56 space-y-3 p-3" key={brand.id}>
                        {findings.length ? (
                          findings.map((claim) => (
                            <article key={claim.id}>
                              <span className="text-muted-foreground text-xs">
                                {claimDisplayStatus(claim)}
                              </span>
                              <p>{claim.display_text ?? claim.text}</p>
                              {claim.statement?.conditions && (
                                <p className="text-muted-foreground text-xs">
                                  适用条件：{claim.statement.conditions}
                                </p>
                              )}
                              {sourceButtons(claim)}
                            </article>
                          ))
                        ) : (
                          <>
                            <p className="text-muted-foreground">
                              {query && cell?.status === "covered"
                                ? "没有匹配的结论"
                                : "未知：暂缺可引用依据"}
                            </p>
                            {canRefine && cell && cell.status !== "covered" && (
                              <button
                                type="button"
                                className="text-primary text-xs underline"
                                onClick={() => onRefine(cell)}
                              >
                                补充这项资料
                              </button>
                            )}
                          </>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {dimensions.length === 0 && (
            <p className="text-muted-foreground p-4">没有匹配的比较项目。</p>
          )}
        </div>
      )}
      {view === "pricing" && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                {["竞品 / 方案", "价格", "计费条件", "依据"].map((label) => (
                  <th className="p-3" key={label}>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visiblePrices.map((price) => {
                const claim = eligible.find(
                  (item) => item.id === price.claim_id,
                )!;
                const binding = claim.evidence_bindings.find(
                  (item) => item.evidence_id === price.evidence_id,
                );
                return (
                  <tr key={price.id} className="border-t">
                    <td className="p-3">
                      {
                        brands.find((brand) => brand.id === claim.competitor_id)
                          ?.name
                      }{" "}
                      · {price.plan_name}
                    </td>
                    <td className="p-3">
                      {price.currency} {price.amount} /{" "}
                      {billingLabel(price.billing_period)}
                    </td>
                    <td className="p-3">
                      {billingLabel(price.billing_unit)}
                      {price.region ? ` · ${price.region}` : " · 地区未注明"}
                    </td>
                    <td className="p-3">
                      <p>{price.official ? "官方资料" : "第三方估计"}</p>
                      <button
                        className="text-primary text-xs underline"
                        onClick={() =>
                          onSource({ evidenceId: price.evidence_id, binding })
                        }
                      >
                        查看原文
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {visiblePrices.length === 0 && (
            <p className="text-muted-foreground p-4">
              {query || dimension || competitor
                ? "没有匹配筛选条件的已核对价格。可以清除筛选重新查看。"
                : "当前没有通过核对的价格，不能据此判断产品免费。"}
            </p>
          )}
        </div>
      )}
      {view === "audience" && (
        <div className="grid gap-3 md:grid-cols-2">
          {shownBrands.map((brand) => {
            const findings = eligible.filter(
              (claim) =>
                claim.competitor_id === brand.id &&
                /用户|场景|定位|迁移|persona|user|position/i.test(
                  claim.dimension,
                ) &&
                (!dimension || claim.dimension === dimension) &&
                (!query ||
                  claim.text.toLowerCase().includes(query.toLowerCase())),
            );
            return (
              <article key={brand.id} className="rounded-lg border p-4">
                <h3 className="font-medium">{brand.name}</h3>
                {findings.length ? (
                  findings.map((claim) => (
                    <div className="mt-3" key={claim.id}>
                      <p className="text-muted-foreground text-xs">
                        {claim.dimension} · {claimDisplayStatus(claim)}
                      </p>
                      <p>{claim.display_text ?? claim.text}</p>
                      {sourceButtons(claim)}
                    </div>
                  ))
                ) : (
                  <p className="text-muted-foreground mt-3 text-sm">
                    用户需求、使用场景与迁移成本尚缺直接依据，不自动补全画像。
                  </p>
                )}
              </article>
            );
          })}
        </div>
      )}
      <p className="text-muted-foreground text-xs">
        已收集 {evidence.length}{" "}
        份资料；可引用结论以最新核对结果为准，发布的报告保留当时版本。
      </p>
    </section>
  );
}
