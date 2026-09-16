import { afterEach, expect, rs, test } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const mocks = rs.hoisted(() => ({ create: rs.fn() }));
rs.mock("@/core/investigations", () => ({
  createInvestigation: mocks.create,
  getResearchOptions: async () => ({
    modes: [
      {
        id: "standard",
        label: "常规研究",
        description: "标准范围",
        base_tokens: 300000,
        extra_tokens: 75000,
        minutes: 30,
      },
      {
        id: "quick",
        label: "快速了解",
        description: "较少资料",
        base_tokens: 180000,
        extra_tokens: 30000,
        minutes: 15,
      },
    ],
    perspectives: [
      { id: "product", label: "产品规划", question: "先做什么" },
      { id: "purchase", label: "采购选型", question: "选择哪款产品" },
    ],
  }),
}));
rs.mock("next/navigation", () => ({
  useRouter: () => ({ back: () => undefined }),
}));

import NewInvestigationPage from "@/app/workspace/investigations/new/page";

afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  rs.clearAllMocks();
});

test("users can explicitly mark pricing as a required research question", async () => {
  mocks.create.mockResolvedValue({ id: "new-study" });
  rs.spyOn(window.location, "assign").mockImplementation(() => undefined);
  render(<NewInvestigationPage />);
  await screen.findByRole("radio", { name: /常规研究/ });
  fireEvent.change(screen.getByLabelText("调研标题"), {
    target: { value: "播放器比较" },
  });
  fireEvent.change(screen.getByLabelText(/决策背景与目标/), {
    target: { value: "比较播放器的收费与功能，决定开发优先级。" },
  });
  fireEvent.change(screen.getByLabelText(/竞品（/), {
    target: { value: "Acme, Beta" },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "定价" }));
  fireEvent.click(screen.getByRole("radio", { name: /快速了解/ }));
  fireEvent.change(screen.getByLabelText(/这份报告主要帮助谁/), {
    target: { value: "purchase" },
  });
  fireEvent.click(screen.getByRole("button", { name: "下一步：确认研究范围" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
  expect(mocks.create.mock.calls[0]?.[0]).toMatchObject({
    mode: "quick",
    scope: { required_dimensions: ["定价"], perspective: "purchase" },
  });
});
