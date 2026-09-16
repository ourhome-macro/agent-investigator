import { afterEach, expect, rs, test } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const mocks = rs.hoisted(() => ({ snapshot: rs.fn(), annotate: rs.fn() }));
rs.mock("@/core/investigations", () => ({
  getEvidenceSnapshot: mocks.snapshot,
  submitAnnotation: mocks.annotate,
}));
rs.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

import { AnnotationDialog } from "@/components/workspace/investigations/annotation-dialog";
import { ComparisonViews } from "@/components/workspace/investigations/comparison-views";
import { ReportReview } from "@/components/workspace/investigations/report-review";
import { SourceDialog } from "@/components/workspace/investigations/source-dialog";
import type {
  Claim,
  Investigation,
  PriceObservation,
  Report,
} from "@/core/investigations/types";

const text = "Acme supports offline playback.";
const claim: Claim = {
  id: "claim-1",
  version: 1,
  competitor_id: "a",
  dimension: "离线",
  text,
  material: true,
  status: "supported",
  publication_eligible: true,
  support_basis: "official_documented",
  independent_source_count: 1,
  evidence_ids: ["e1"],
  evidence_bindings: [
    {
      evidence_id: "e1",
      relation: "supports",
      verbatim_quote: text,
      quote_start: 7,
      quote_end: 7 + text.length,
      snapshot_sha256: "hash-1",
      validation_status: "verified",
      entailment_status: "entails",
    },
  ],
};
const investigation = {
  id: "inv-1",
  scope: { competitors: ["Acme", "Beta"], dimensions: ["离线"] },
  resource_policy: {
    refinement_tokens: 150000,
    refinement_minutes: 15,
    max_annotations: 3,
  },
} as Investigation;
const report: Report = {
  id: "report-1",
  version: 1,
  status: "review",
  created_at: "2026-09-16",
  rendered_markdown: text,
  structured_data: {
    claim_versions: { "claim-1": 1 },
    sections: [
      {
        id: "feature_matrix",
        type: "feature_matrix",
        title: "功能对比",
        markdown: text,
        claim_ids: ["claim-1"],
        evidence_ids: ["e1"],
      },
    ],
  },
};

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
  window.getSelection()?.removeAllRanges();
});

test("comparison preserves unknown cells and opens a quote-specific source", () => {
  const open = rs.fn();
  render(
    <ComparisonViews
      investigation={investigation}
      claims={[claim]}
      coverage={[
        {
          competitor_id: "a",
          competitor: "Acme",
          dimension: "离线",
          status: "covered",
          claim_ids: ["claim-1"],
        },
        {
          competitor_id: "b",
          competitor: "Beta",
          dimension: "离线",
          status: "missing",
          claim_ids: [],
        },
      ]}
      pricing={[]}
      evidence={[]}
      onSource={open}
      onRefine={() => undefined}
      canRefine={false}
    />,
  );
  expect(screen.getByText("未知：暂缺可引用依据")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "原文 1" }));
  expect(open).toHaveBeenCalledWith({
    evidenceId: "e1",
    binding: claim.evidence_bindings[0],
  });
  fireEvent.change(screen.getByLabelText("搜索对比结论"), {
    target: { value: "missing-keyword" },
  });
  expect(screen.getByText("没有匹配的比较项目。")).toBeDefined();
});

test("pricing filters explain empty matches instead of showing a blank table", () => {
  render(
    <ComparisonViews
      investigation={investigation}
      claims={[claim]}
      coverage={[
        {
          competitor_id: "a",
          competitor: "Acme",
          dimension: "离线",
          status: "covered",
          claim_ids: [claim.id],
        },
      ]}
      pricing={[
        {
          id: "p1",
          claim_id: claim.id,
          evidence_id: "e1",
          plan_name: "Pro",
          amount: "10",
          currency: "USD",
          billing_period: "month",
          billing_unit: "seat",
          official: true,
        } as PriceObservation,
      ]}
      evidence={[]}
      onSource={() => undefined}
      onRefine={() => undefined}
      canRefine={false}
    />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "价格与方案" }));
  expect(screen.getByText(/Pro/)).toBeDefined();
  fireEvent.change(screen.getByLabelText("搜索对比结论"), {
    target: { value: "missing-plan" },
  });
  expect(
    screen.getByText("没有匹配筛选条件的已核对价格。可以清除筛选重新查看。"),
  ).toBeDefined();
});

test("source viewer validates the snapshot and highlights the recorded quote", async () => {
  mocks.snapshot.mockResolvedValue({
    evidence_id: "e1",
    title: "Official docs",
    source_url: "https://acme.test/docs",
    retrieved_at: "2026-09-16T00:00:00Z",
    content: `prefix ${text} suffix`,
    sha256: "hash-1",
  });
  render(
    <SourceDialog
      investigationId="inv-1"
      selection={{ evidenceId: "e1", binding: claim.evidence_bindings[0] }}
      onClose={() => undefined}
    />,
  );
  await screen.findByText("Official docs");
  expect(document.querySelector("mark")?.textContent).toBe(text);
});

test("source viewer rejects a snapshot that does not match the cited version", async () => {
  mocks.snapshot.mockResolvedValue({
    evidence_id: "e1",
    title: "Changed source",
    content: text,
    sha256: "different-hash",
  });
  render(
    <SourceDialog
      investigationId="inv-1"
      selection={{ evidenceId: "e1", binding: claim.evidence_bindings[0] }}
      onClose={() => undefined}
    />,
  );
  expect((await screen.findByRole("alert")).textContent).toContain("原文版本");
  expect(document.querySelector("mark")).toBeNull();
  expect(screen.queryByText("Changed source")).toBeNull();
});

test("selecting report text binds the section and current claim version", () => {
  const refine = rs.fn();
  render(
    <ReportReview
      report={report}
      claims={[claim]}
      canRefine
      onRefine={refine}
    />,
  );
  const element = screen.getByText(text);
  const range = document.createRange();
  range.selectNodeContents(element);
  window.getSelection()?.addRange(range);
  fireEvent.mouseUp(element);
  fireEvent.click(screen.getByRole("button", { name: "对选中文字补研" }));
  expect(refine).toHaveBeenCalledWith({
    sectionKey: "feature_matrix",
    quote: text,
    claims: [claim],
  });
});

test("feedback retries reuse their idempotency key and preserve selection", async () => {
  mocks.annotate
    .mockRejectedValueOnce(new Error("Network interrupted"))
    .mockResolvedValueOnce({ id: "request-1" });
  const closed = rs.fn();
  render(
    <AnnotationDialog
      investigation={investigation}
      report={report}
      target={{ sectionKey: "feature_matrix", quote: text, claims: [claim] }}
      onClose={closed}
      onSubmitted={async () => undefined}
    />,
  );
  fireEvent.change(screen.getByLabelText("你的疑问或补充要求"), {
    target: { value: "请核对新版本是否支持" },
  });
  fireEvent.click(screen.getByRole("button", { name: "确认并开始补研" }));
  await screen.findByRole("alert");
  fireEvent.click(screen.getByRole("button", { name: "确认并开始补研" }));
  await waitFor(() => expect(closed).toHaveBeenCalled());
  const first = mocks.annotate.mock.calls[0]?.[1];
  const second = mocks.annotate.mock.calls[1]?.[1];
  expect(first).toEqual(second);
  expect(first).toMatchObject({
    report_version: 1,
    claim_id: "claim-1",
    claim_version: 1,
    selected_text: text,
  });
});
