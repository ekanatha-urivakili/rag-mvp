import { afterEach, describe, expect, it, vi } from "vitest";

const base = {
  SESSION_SECRET: "test-secret-test-secret-test-secret-0123456789",
  APP_ORIGIN: "https://app.example.com",
  ENV: "prod",
};

async function load(extra: Record<string, string>) {
  vi.resetModules();
  for (const [k, v] of Object.entries({ ...base, ...extra })) vi.stubEnv(k, v);
  const { env } = await import("@/lib/env");
  return env;
}

afterEach(() => vi.unstubAllEnvs());

describe("env", () => {
  it("refuses prod without a trusted proxy hop, so per-IP auth limits are not shared by every browser", async () => {
    const env = await load({ TRUSTED_PROXY_HOPS: "0" });
    expect(() => env()).toThrow(/TRUSTED_PROXY_HOPS/);
  });

  it("accepts prod behind one trusted ingress", async () => {
    const env = await load({ TRUSTED_PROXY_HOPS: "1" });
    expect(env().TRUSTED_PROXY_HOPS).toBe(1);
  });
});
