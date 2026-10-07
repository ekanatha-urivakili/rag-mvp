import "server-only";

import { NextResponse } from "next/server";
import { isIP } from "node:net";

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
 * trusted hops, so take the entry `hops` from the right. Next.js preserves a supplied header.
 */
export function clientIp(headers: Headers, hops: number): string | null {
  if (hops === 0) return null;
  const parts = (headers.get("x-forwarded-for") ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  const ip = parts[parts.length - hops];
  return ip && isIP(ip) ? ip : null;
}

const UUID = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";

/**
 * The only API routes the browser may reach through the BFF (least privilege, OWASP API9).
 * The API enforces tenant, role and user-session permissions on each allowed route.
 */
const ALLOWED: ReadonlyArray<readonly [string, RegExp]> = [
  ["GET", /^me$/],
  ["PATCH", /^me$/],
  ["POST", /^auth\/password\/change$/],
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
  ["POST", /^receipts$/],
  ["GET", new RegExp(`^receipts/${UUID}$`)],
  ["GET", /^members$/],
  ["PATCH", new RegExp(`^members/${UUID}$`)],
  ["DELETE", new RegExp(`^members/${UUID}$`)],
  ["GET", /^invitations$/],
  ["POST", /^invitations$/],
  ["GET", /^api-keys$/],
  ["POST", /^api-keys$/],
  ["DELETE", new RegExp(`^api-keys/${UUID}$`)],
  ["GET", /^audit-log$/],
];

/** Maps `/api/v1/<segments>` to an upstream `/v1/...` path, or null if not allowlisted. */
export function resolveProxyTarget(method: string, segments: readonly string[]): string | null {
  // Segments arrive decoded; reject anything that could change the path shape (traversal, encoded slashes).
  if (segments.some((s) => !/^[A-Za-z0-9-]+$/.test(s))) return null;
  const path = segments.join("/");
  return ALLOWED.some(([m, re]) => m === method && re.test(path)) ? `/v1/${path}` : null;
}

export class BodyTooLarge extends Error {}

export function boundedBody(body: ReadableStream<Uint8Array> | null, maxBytes: number) {
  let total = 0;
  let exceeded = false;
  const stream = body?.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        total += chunk.byteLength;
        if (total > maxBytes) {
          exceeded = true;
          controller.error(new BodyTooLarge());
        } else controller.enqueue(chunk);
      },
    }),
  );
  return { stream: stream ?? null, exceeded: () => exceeded };
}

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
