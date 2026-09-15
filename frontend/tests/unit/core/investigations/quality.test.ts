import { describe, expect, it } from "@rstest/core";

import {
  claimDisplayStatus,
  shouldPollInvestigation,
} from "@/core/investigations/quality";

describe("Research quality presentation", () => {
  it("does not present an audit-blocked supported claim as verified", () => {
    expect(
      claimDisplayStatus({ status: "supported", publication_eligible: false }),
    ).toBe("仍需核实");
    expect(
      claimDisplayStatus({ status: "superseded", publication_eligible: false }),
    ).toBe("已替代");
    expect(
      claimDisplayStatus({ status: "supported", publication_eligible: true }),
    ).toBe("多个来源支持");
  });

  it("stops polling at approval gates and terminal states", () => {
    expect(shouldPollInvestigation("published")).toBe(false);
    expect(shouldPollInvestigation("failed")).toBe(false);
    expect(shouldPollInvestigation("awaiting_publish_approval")).toBe(false);
    expect(shouldPollInvestigation("reworking")).toBe(true);
  });
});
