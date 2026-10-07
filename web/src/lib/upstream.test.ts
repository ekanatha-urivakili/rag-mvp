import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

beforeAll(() => {
  process.env.SESSION_SECRET = "test-secret-test-secret-test-secret-0123456789";
  process.env.API_URL = "http://api.test";
});

afterEach(() => vi.unstubAllGlobals());

const tokens = { access_token: "new-at", refresh_token: "new-rt", expires_in: 900 };

describe("refreshTokens", async () => {
  const { refreshTokens } = await import("@/lib/upstream");

  it("shares one upstream call between concurrent and late requests with the same token", async () => {
    const fetchMock = vi.fn(async () => Response.json(tokens));
    vi.stubGlobal("fetch", fetchMock);
    const h = new Headers();
    const results = await Promise.all([refreshTokens("rt-1", h), refreshTokens("rt-1", h), refreshTokens("rt-1", h)]);
    const late = await refreshTokens("rt-1", h); // a request that still carried the old cookie
    expect(fetchMock).toHaveBeenCalledTimes(1); // replaying rt-1 would make the API revoke the session
    for (const r of [...results, late]) expect(r).toEqual({ kind: "ok", tokens });
  });

  it("reports invalid only for a 401, and retries after an outage", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 401 })),
    );
    expect(await refreshTokens("rt-revoked", new Headers())).toEqual({ kind: "invalid" });

    const down = vi.fn(async () => {
      throw new TypeError("connect ECONNREFUSED");
    });
    vi.stubGlobal("fetch", down);
    expect(await refreshTokens("rt-2", new Headers())).toEqual({ kind: "unavailable" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(tokens)),
    );
    expect(await refreshTokens("rt-2", new Headers())).toEqual({ kind: "ok", tokens });
  });
});
