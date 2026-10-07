import "server-only";

import { env } from "@/lib/env";
import { clientIp } from "@/lib/security";
import type { Session, TokenResponse } from "@/lib/session";

type UpstreamInit = {
  method: string;
  incoming: Headers;
  session?: Session | null;
  json?: unknown;
  body?: ReadableStream<Uint8Array> | null;
  contentType?: string | null;
  signal?: AbortSignal;
};

/** Calls the FastAPI backend. The only place that attaches bearer tokens. */
export async function upstream(path: string, init: UpstreamInit): Promise<Response> {
  const e = env();
  const headers = new Headers({ Accept: "application/json, text/event-stream" });
  if (init.session) headers.set("Authorization", `Bearer ${init.session.accessToken}`);
  const ip = clientIp(init.incoming, e.TRUSTED_PROXY_HOPS);
  // The API trusts this only from the BFF's address (uvicorn --forwarded-allow-ips).
  if (ip) headers.set("X-Forwarded-For", ip);
  let body: BodyInit | null = init.body ?? null;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  } else if (init.contentType) {
    headers.set("Content-Type", init.contentType);
  }
  const request: RequestInit & { duplex?: "half" } = {
    method: init.method,
    headers,
    body,
    cache: "no-store",
    redirect: "manual",
    signal: init.signal ?? AbortSignal.timeout(30_000),
  };
  if (body instanceof ReadableStream) request.duplex = "half"; // undici needs this to stream uploads
  return fetch(new URL(path, e.API_URL), request);
}

// --- Refresh with de-duplication ------------------------------------------------------------
// The API rotates refresh tokens and treats reuse of a rotated token as theft (it revokes the whole
// session). Parallel requests carrying the same expiring cookie must therefore share ONE refresh call,
// and late requests still holding the old token must get the same result instead of replaying it.
// This is per process: with several web replicas, use sticky sessions (see docs/rag_mvp_architecture.md §6).

export type RefreshResult = { kind: "ok"; tokens: TokenResponse } | { kind: "invalid" } | { kind: "unavailable" };

const GRACE_MS = 60_000;
const recent = new Map<string, { result: Promise<RefreshResult>; started: number }>();

export async function refreshTokens(refreshToken: string, incoming: Headers): Promise<RefreshResult> {
  const now = Date.now();
  for (const [k, v] of recent) if (now - v.started > GRACE_MS) recent.delete(k);
  const hit = recent.get(refreshToken);
  if (hit) return hit.result;

  const result = (async (): Promise<RefreshResult> => {
    try {
      const r = await upstream("/v1/auth/refresh", { method: "POST", incoming, json: { refresh_token: refreshToken } });
      if (r.ok) return { kind: "ok", tokens: (await r.json()) as TokenResponse };
      return r.status === 401 ? { kind: "invalid" } : { kind: "unavailable" };
    } catch {
      return { kind: "unavailable" };
    }
  })();
  recent.set(refreshToken, { result, started: now });
  const settled = await result;
  if (settled.kind === "unavailable") recent.delete(refreshToken); // allow a retry on the next request
  return settled;
}
