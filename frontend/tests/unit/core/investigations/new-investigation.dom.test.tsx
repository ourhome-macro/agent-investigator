import { afterEach, expect, rs, test } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

const mocks = rs.hoisted(() => ({ create: rs.fn() }));
rs.mock("@/core/investigations", () => ({ createInvestigation: mocks.create }));
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
  fireEvent.click(screen.getByRole("button", { name: "下一步：确认研究范围" }));
  await waitFor(() => expect(mocks.create).toHaveBeenCalledTimes(1));
  expect(mocks.create.mock.calls[0]?.[0]).toMatchObject({
    scope: { required_dimensions: ["定价"] },
  });
});
