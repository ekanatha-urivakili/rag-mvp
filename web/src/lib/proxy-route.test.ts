import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ upstream: vi.fn() }));
vi.mock("@/lib/env", () => ({ env: () => ({ appOrigin: "http://localhost:3000" }) }));
vi.mock("next/headers", () => ({ cookies: async () => ({ get: () => ({ value: "sealed" }) }) }));
vi.mock("@/lib/session", () => ({
  openSession: async () => ({ accessToken: "at", refreshToken: "rt", accessExpiresAt: 123 }),
  sessionCookieName: () => "rag_session",
}));
vi.mock("@/lib/upstream", () => ({ upstream: mocks.upstream }));

const { POST, GET } = await import("@/app/api/v1/[...path]/route");
const context = { params: Promise.resolve({ path: ["chat"] }) };

beforeEach(() => {
  mocks.upstream.mockReset();
  mocks.upstream.mockImplementation(async (_path: string, init: { body: ReadableStream<Uint8Array> | null }) => {
    await new Response(init.body).arrayBuffer();
    return Response.json({ ok: true });
  });
});

describe("BFF route resource limits", () => {
  it("returns 413 for chunked bodies exceeding the JSON cap", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new Uint8Array(10_000));
        c.enqueue(new Uint8Array(10_000));
        c.close();
      },
    });
    const req = new NextRequest(
      new Request("http://localhost:3000/api/v1/chat", {
        method: "POST",
        headers: { Origin: "http://localhost:3000", "Content-Type": "application/json", "Content-Length": "1" },
        body: stream,
        duplex: "half",
      } as RequestInit & { duplex: "half" }),
    );
    expect((await POST(req, context)).status).toBe(413);
  });
  it("rejects overlong queries instead of silently removing filters", async () => {
    const req = new NextRequest(`http://localhost:3000/api/v1/documents?q=${"x".repeat(2050)}`);
    expect((await GET(req, { params: Promise.resolve({ path: ["documents"] }) })).status).toBe(414);
    expect(mocks.upstream).not.toHaveBeenCalled();
  });
  it("rejects cross-origin mutations before making an upstream call", async () => {
    const req = new NextRequest("http://localhost:3000/api/v1/chat", {
      method: "POST",
      headers: { Origin: "http://evil.example" },
    });
    expect((await POST(req, context)).status).toBe(403);
    expect(mocks.upstream).not.toHaveBeenCalled();
  });
});
