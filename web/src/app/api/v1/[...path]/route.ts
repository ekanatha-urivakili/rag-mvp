import { cookies } from "next/headers";
import type { NextRequest } from "next/server";
import { env } from "@/lib/env";
import { boundedBody, isSameOrigin, jsonError, resolveProxyTarget } from "@/lib/security";
import { openSession, sessionCookieName } from "@/lib/session";
import { upstream } from "@/lib/upstream";

// Upload cap (25 MB) plus multipart overhead; the API enforces the exact per-file and actual-byte limits.
const MAX_BODY_BYTES = 30 * 1024 * 1024;
const PASS_HEADERS = ["content-type", "retry-after", "x-request-id", "content-disposition"];

async function handle(req: NextRequest, ctx: RouteContext<"/api/v1/[...path]">): Promise<Response> {
  const method = req.method;
  if (method !== "GET" && !isSameOrigin(req.headers, env().appOrigin)) {
    return jsonError(403, "csrf", "Cross-site request blocked");
  }
  const target = resolveProxyTarget(method, (await ctx.params).path);
  if (!target) return jsonError(404, "not_found", "Not found");

  const session = await openSession((await cookies()).get(sessionCookieName())?.value);
  if (!session) return jsonError(401, "unauthorized", "Not signed in");

  const cap = target === "/v1/documents" && method === "POST" ? MAX_BODY_BYTES : 16 * 1024;
  const declared = Number(req.headers.get("content-length") ?? "0");
  if (declared > cap) return jsonError(413, "payload_too_large", "Request body too large");

  const contentType = req.headers.get("content-type");
  if (req.body && contentType && !/^(application\/json|multipart\/form-data)\b/i.test(contentType)) {
    return jsonError(415, "unsupported_media_type", "Unsupported content type");
  }

  if (req.nextUrl.search.length > 2048) return jsonError(414, "uri_too_long", "Query string too long");
  const search = req.nextUrl.search;
  const isChat = target === "/v1/chat";
  const body = boundedBody(method === "GET" || method === "DELETE" ? null : req.body, cap);
  let r: Response;
  try {
    r = await upstream(target + search, {
      method,
      incoming: req.headers,
      session,
      body: body.stream,
      contentType,
      // Chat streams for minutes and must stop when the browser disconnects; everything else times out.
      signal: AbortSignal.any([req.signal, AbortSignal.timeout(isChat ? 300_000 : 30_000)]),
    });
  } catch {
    if (body.exceeded()) return jsonError(413, "payload_too_large", "Request body too large");
    return jsonError(503, "unavailable", "The service is unavailable. Please try again.");
  }
  if (body.exceeded()) return jsonError(413, "payload_too_large", "Request body too large");

  const headers = new Headers({ "Cache-Control": "no-store" });
  for (const h of PASS_HEADERS) {
    const v = r.headers.get(h);
    if (v) headers.set(h, v);
  }
  if (isChat) headers.set("X-Accel-Buffering", "no");
  // Streams the upstream body (SSE for chat) without buffering. Never forwards upstream Set-Cookie.
  return new Response(r.status === 204 ? null : r.body, { status: r.status, headers });
}

export { handle as GET, handle as POST, handle as PATCH, handle as DELETE };
