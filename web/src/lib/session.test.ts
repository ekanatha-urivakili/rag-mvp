import { beforeAll, describe, expect, it } from "vitest";

beforeAll(() => {
  process.env.SESSION_SECRET = "test-secret-test-secret-test-secret-0123456789";
  process.env.APP_ORIGIN = "http://localhost:3000";
});

describe("session cookie", async () => {
  const { needsRefresh, openSession, sealSession, sessionCookieName } = await import("@/lib/session");
  const session = { accessToken: "a.b.c", refreshToken: "r".repeat(43), accessExpiresAt: 2_000_000_000 };

  it("round-trips and is opaque", async () => {
    const sealed = await sealSession(session);
    expect(sealed).not.toContain(session.refreshToken);
    expect(sealed).not.toContain(session.accessToken);
    await expect(openSession(sealed)).resolves.toEqual(session);
  });

  it("rejects tampering and garbage", async () => {
    const sealed = await sealSession(session);
    const parts = sealed.split(".");
    parts[3] = parts[3]!.replace(/^./, (ch) => (ch === "A" ? "B" : "A"));
    await expect(openSession(parts.join("."))).resolves.toBeNull();
    await expect(openSession("not-a-jwe")).resolves.toBeNull();
    await expect(openSession(undefined)).resolves.toBeNull();
    await expect(openSession("x".repeat(5000))).resolves.toBeNull();
  });

  it("refreshes shortly before the access token expires", () => {
    expect(needsRefresh({ ...session, accessExpiresAt: 1000 }, 900)).toBe(false);
    expect(needsRefresh({ ...session, accessExpiresAt: 1000 }, 950)).toBe(true);
  });

  it("uses a plain cookie name over http (no __Host- without Secure)", () => {
    expect(sessionCookieName()).toBe("rag_session");
  });
});
