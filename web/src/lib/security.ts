import "server-only";

import { NextResponse } from "next/server";

/** Same error envelope as the API, so the browser handles one shape. Never includes internals. */
export function jsonError(status: number, code: string, message: string): NextResponse {
  return NextResponse.json({ error: { code, message } }, { status, headers: { "Cache-Control": "no-store" } });
}

/**
 * CSRF defence in depth on top of SameSite=Strict: mutating requests must carry our exact Origin,
 * or (older browsers without Origin) Sec-Fetch-Site: same-origin. Anything else is rejected.
 */
export function isSameOrigin(headers: Headers, appOrigin: string): boolean {
  const origin = headers.get("origin");
  if (origin !== null) return origin === appOrigin;
  return headers.get("sec-fetch-site") === "same-origin";
}

/**
 * Client IP for API rate limiting. X-Forwarded-For is client-controlled except for the entries appended by
 * trusted hops, so take the entry `hops` from the right (Next.js appends the socket peer when absent).
 */
export function clientIp(headers: Headers, hops: number): string | null {
  const parts = (headers.get("x-forwarded-for") ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  const ip = parts[parts.length - hops];
  return ip && /^[0-9a-fA-F:.]{2,45}$/.test(ip) ? ip : null;
}

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/**
 * The only API routes the browser may reach through the BFF (least privilege, OWASP API9).
 * Admin routes are added here when their pages move to this app.
 */
const ALLOWED: ReadonlyArray<readonly [string, RegExp]> = [
  ["GET", /^me$/],
  ["POST", /^chat$/],
  ["GET", /^conversations$/],
  ["GET", new RegExp(`^conversations/${UUID}$`)],
  ["PATCH", new RegExp(`^conversations/${UUID}$`)],
  ["DELETE", new RegExp(`^conversations/${UUID}$`)],
  ["POST", new RegExp(`^messages/${UUID}/feedback$`)],
  ["GET", /^documents$/],
  ["POST", /^documents$/],
  ["GET", new RegExp(`^documents/${UUID}$`)],
  ["DELETE", new RegExp(`^documents/${UUID}$`)],
  ["GET", /^receipts$/],
  ["GET", new RegExp(`^receipts/${UUID}$`)],
];

/** Maps `/api/v1/<segments>` to an upstream `/v1/...` path, or null if not allowlisted. */
export function resolveProxyTarget(method: string, segments: readonly string[]): string | null {
  // Segments arrive decoded; reject anything that could change the path shape (traversal, encoded slashes).
  if (segments.some((s) => !/^[A-Za-z0-9-]+$/.test(s))) return null;
  const path = segments.join("/");
  return ALLOWED.some(([m, re]) => m === method && re.test(path)) ? `/v1/${path}` : null;
}

export class BodyTooLarge extends Error {}

/** Reads a JSON body with a hard byte cap, including chunked bodies without Content-Length (OWASP API4). */
export async function readJsonBounded(req: Request, maxBytes: number): Promise<unknown> {
  const declared = Number(req.headers.get("content-length") ?? "0");
  if (declared > maxBytes) throw new BodyTooLarge();
  if (!req.body) return null;
  const reader = req.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) {
      await reader.cancel();
      throw new BodyTooLarge();
    }
    chunks.push(value);
  }
  const text = new TextDecoder().decode(Buffer.concat(chunks));
  return text ? JSON.parse(text) : null;
}
