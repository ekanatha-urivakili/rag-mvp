import { describe, expect, it } from "vitest";
import { BodyTooLarge, clientIp, isSameOrigin, readJsonBounded, resolveProxyTarget } from "@/lib/security";

const ID = "3f2b8c1e-9a4d-4e6f-8b7a-1c2d3e4f5a6b";

describe("resolveProxyTarget (BFF allowlist)", () => {
  it.each([
    ["GET", ["me"], "/v1/me"],
    ["POST", ["chat"], "/v1/chat"],
    ["GET", ["conversations"], "/v1/conversations"],
    ["PATCH", ["conversations", ID], `/v1/conversations/${ID}`],
    ["DELETE", ["conversations", ID], `/v1/conversations/${ID}`],
    ["POST", ["messages", ID, "feedback"], `/v1/messages/${ID}/feedback`],
    ["POST", ["documents"], "/v1/documents"],
  ])("allows %s %j", (method, segments, expected) => {
    expect(resolveProxyTarget(method, segments)).toBe(expected);
  });

  it.each([
    ["GET", ["members"]], // admin routes are not exposed until their pages exist
    ["POST", ["api-keys"]],
    ["GET", ["audit-log"]],
    ["POST", ["auth", "login"]], // auth goes through /api/auth/* only
    ["PATCH", ["documents", ID]], // wrong method
    ["GET", ["conversations", "not-a-uuid"]],
    ["GET", ["conversations", ID, "..", "..", "members"]],
    ["GET", ["conversations", `${ID}%2F..`]],
    ["GET", ["documents", "..%2Fmembers"]],
    ["GET", []],
  ])("blocks %s %j", (method, segments) => {
    expect(resolveProxyTarget(method, segments)).toBeNull();
  });
});

describe("isSameOrigin (CSRF)", () => {
  const app = "https://rag.example.com";
  it("accepts our exact origin", () => {
    expect(isSameOrigin(new Headers({ origin: app }), app)).toBe(true);
  });
  it.each(["https://evil.example.com", "https://rag.example.com.evil.io", "http://rag.example.com", "null"])(
    "rejects origin %s",
    (origin) => expect(isSameOrigin(new Headers({ origin }), app)).toBe(false),
  );
  it("falls back to Sec-Fetch-Site only when Origin is absent", () => {
    expect(isSameOrigin(new Headers({ "sec-fetch-site": "same-origin" }), app)).toBe(true);
    expect(isSameOrigin(new Headers({ "sec-fetch-site": "cross-site" }), app)).toBe(false);
    expect(isSameOrigin(new Headers(), app)).toBe(false);
  });
});

describe("clientIp", () => {
  it("takes the entry appended by the trusted hop, ignoring client-supplied prefixes", () => {
    const h = new Headers({ "x-forwarded-for": "6.6.6.6, 203.0.113.7" });
    expect(clientIp(h, 1)).toBe("203.0.113.7");
    expect(clientIp(h, 2)).toBe("6.6.6.6");
  });
  it("rejects garbage", () => {
    expect(clientIp(new Headers({ "x-forwarded-for": "<script>" }), 1)).toBeNull();
    expect(clientIp(new Headers(), 1)).toBeNull();
  });
});

describe("readJsonBounded", () => {
  it("parses small bodies", async () => {
    const req = new Request("http://x", { method: "POST", body: JSON.stringify({ a: 1 }) });
    await expect(readJsonBounded(req, 100)).resolves.toEqual({ a: 1 });
  });
  it("rejects bodies over the cap even without Content-Length", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode("x".repeat(80)));
        c.enqueue(new TextEncoder().encode("x".repeat(80)));
        c.close();
      },
    });
    const req = new Request("http://x", { method: "POST", body: stream, duplex: "half" } as RequestInit);
    await expect(readJsonBounded(req, 100)).rejects.toBeInstanceOf(BodyTooLarge);
  });
});
