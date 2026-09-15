import { afterEach, expect, rs, test } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const mocks = rs.hoisted(() => ({ fetch: rs.fn(), approved: false }));
rs.mock("@/core/api/fetcher", () => ({ fetch: mocks.fetch }));
rs.mock("next/navigation", () => ({
  useParams: () => ({ id: "investigation-one" }),
}));
rs.mock("@/components/workspace/messages/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

import InvestigationPage from "@/app/workspace/investigations/[id]/page";

afterEach(() => {
  cleanup();
  rs.clearAllMocks();
  mocks.approved = false;
});

test("partial publication stays explicit and blocked claims are not shown as verified", async () => {
  mocks.fetch.mockImplementation(async (url: string, options?: RequestInit) => {
    let payload: unknown = [];
    if (url.endsWith("/reports/1/approve") && options?.method === "POST")
      mocks.approved = true;
    if (url.endsWith("/investigation-one"))
      payload = {
        id: "investigation-one",
        title: "Player study",
        brief: "Research is incomplete",
        status: mocks.approved ? "published" : "awaiting_publish_approval",
        token_used: 100,
        token_budget: 300000,
        rework_round: 1,
        failure_retry_count: 0,
        scope: { competitors: ["Acme", "Beta"], dimensions: ["离线"] },
      };
    if (url.endsWith("/coverage"))
      payload = [
        {
          competitor_id: "beta",
          competitor: "Beta",
          dimension: "离线",
          status: "missing",
          claim_ids: [],
        },
      ];
    if (url.endsWith("/claims"))
      payload = [
        {
          id: "claim-one",
          text: "Unresolved claim",
          dimension: "离线",
          status: "supported",
          publication_eligible: false,
          independent_source_count: 2,
          evidence_ids: [],
          evidence_bindings: [],
        },
      ];
    if (url.includes("/reports/"))
      payload = {
        id: "report-one",
        version: 1,
        status: mocks.approved ? "published" : "review",
        structured_data: { partial: true },
        rendered_markdown: "Research status and remaining gaps",
      };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  render(<InvestigationPage />);
  const publish = await screen.findByRole("button", {
    name: "确认发布部分结果",
  });
  expect(screen.getByText("缺少资料")).toBeDefined();
  expect(screen.getByText("仍需核实")).toBeDefined();
  expect(screen.getByText("可引用的结论").parentElement?.textContent).toContain(
    "0",
  );
  fireEvent.click(publish);
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "确认发布部分结果" }),
    ).toBeNull(),
  );
  expect(screen.getByText(/研究未完成 · 已有结果/)).toBeDefined();
});

test("workspace explains evidence levels and keeps runtime identifiers in closed details", async () => {
  mocks.fetch.mockImplementation(async (url: string) => {
    let payload: unknown = [];
    if (url.endsWith("/investigation-one"))
      payload = {
        id: "investigation-one",
        title: "产品比较",
        brief: "比较功能与价格",
        status: "awaiting_publish_approval",
        token_used: 100,
        token_budget: 1000,
        rework_round: 0,
        failure_retry_count: 0,
        scope: { competitors: ["Acme", "Beta"], dimensions: ["功能", "定价"] },
      };
    if (url.endsWith("/claims"))
      payload = ["official_documented", "vendor_stated", "user_reported"].map(
        (support_basis, index) => ({
          id: `claim-${index}`,
          text: `来源陈述 ${index}`,
          dimension: "功能",
          status: "supported",
          publication_eligible: true,
          support_basis,
          independent_source_count: 1,
          evidence_ids: [],
          evidence_bindings: [],
        }),
      );
    if (url.endsWith("/orchestration/items"))
      payload = [
        {
          id: "task-1",
          item_key: "audit:opaque-task",
          stage: "auditing",
          role: "evidence-auditor",
          status: "succeeded",
          attempt: 1,
          max_attempts: 3,
        },
      ];
    if (url.endsWith("/audit/issues"))
      payload = [
        {
          id: "issue-1",
          claim_id: null,
          section_id: null,
          severity: "warning",
          rule: "missing_optional_detail",
          reason: "Optional detail unavailable",
          required_action: "note",
          status: "open",
          blocking: false,
        },
      ];
    if (url.endsWith("/reports/latest"))
      payload = {
        id: "report-1",
        version: 1,
        structured_data: {
          partial: false,
          completion_status: "completed_with_gaps",
        },
        rendered_markdown: "已知缺口：价格尚未公开。",
      };
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  render(<InvestigationPage />);
  await screen.findByText("官方资料说明");
  expect(screen.getByText("厂商自述")).toBeDefined();
  expect(screen.getByText("个别用户反馈")).toBeDefined();
  expect(screen.getByText(/尚未获得独立实测验证/)).toBeDefined();
  expect(screen.getByText(/不能据此判断所有用户/)).toBeDefined();
  expect(screen.getByText(/研究完成，含已知缺口/)).toBeDefined();
  expect(screen.getByText("补充说明，不阻止其他结论使用")).toBeDefined();
  expect(screen.getByRole("heading", { name: "执行进度" })).toBeDefined();
  expect(
    screen.queryByRole("heading", { name: "Agent / Batch Items" }),
  ).toBeNull();
  expect(
    screen.getByText("任务编号：audit:opaque-task").closest("details")?.open,
  ).toBe(false);
});
