import { describe, expect, test } from "@rstest/core";

import { getMemoryGatewayBaseURL } from "@/core/memory/gateway-url";

describe("memory gateway URL", () => {
  test("prefers the server-only internal gateway URL", () => {
    expect(
      getMemoryGatewayBaseURL({
        DEER_FLOW_INTERNAL_GATEWAY_BASE_URL: "http://127.0.0.1:18001",
        NEXT_PUBLIC_BACKEND_BASE_URL: "http://127.0.0.1:9999",
      }),
    ).toBe("http://127.0.0.1:18001");
  });

  test("falls back to the public backend URL", () => {
    expect(
      getMemoryGatewayBaseURL({
        NEXT_PUBLIC_BACKEND_BASE_URL: "http://127.0.0.1:9001/",
      }),
    ).toBe("http://127.0.0.1:9001");
  });

  test("uses the standard local Gateway when neither override exists", () => {
    expect(getMemoryGatewayBaseURL({})).toBe("http://127.0.0.1:8001");
  });
});
