type GatewayEnvironment = Readonly<Record<string, string | undefined>>;

function configuredURL(value: string | undefined): string | undefined {
  const normalized = value?.trim().replace(/\/+$/, "");
  if (!normalized) {
    return undefined;
  }
  return normalized;
}

export function getMemoryGatewayBaseURL(
  env: GatewayEnvironment = process.env,
): string {
  return (
    configuredURL(env.DEER_FLOW_INTERNAL_GATEWAY_BASE_URL) ??
    configuredURL(env.NEXT_PUBLIC_BACKEND_BASE_URL) ??
    "http://127.0.0.1:8001"
  );
}
