import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod";
import { env } from "@/lib/env";
import { BodyTooLarge, isSameOrigin, jsonError, readJsonBounded } from "@/lib/security";
import {
  openSession,
  sealSession,
  sessionCookieName,
  sessionCookieOptions,
  sessionFromTokens,
  type Session,
  type TokenResponse,
} from "@/lib/session";
import { upstream } from "@/lib/upstream";

const email = z.email().max(320);
const password = z.string().min(1).max(128);
const linkToken = z.string().min(16).max(256);

/** Each public auth action: strict input schema (unknown keys rejected) → upstream path. */
const ACTIONS = {
  login: { path: "/v1/auth/login", body: z.strictObject({ email, password }), issuesSession: true },
  signup: { path: "/v1/auth/signup", body: z.strictObject({ email }), issuesSession: false },
  "signup-verify": {
    path: "/v1/auth/signup/verify",
    body: z.strictObject({ token: linkToken, password, workspace_name: z.string().trim().min(1).max(160) }),
    issuesSession: true,
  },
  forgot: { path: "/v1/auth/password/forgot", body: z.strictObject({ email }), issuesSession: false },
  reset: {
    path: "/v1/auth/password/reset",
    body: z.strictObject({ token: linkToken, new_password: password }),
    issuesSession: false,
  },
  "accept-invite": {
    path: "/v1/invitations/accept",
    body: z.strictObject({ token: linkToken, password: password.nullable() }),
    issuesSession: true,
  },
} as const;

const switchTenant = z.strictObject({ tenant_id: z.uuid() });
const MAX_BODY = 16 * 1024;

async function withSession(res: NextResponse, tokens: TokenResponse): Promise<NextResponse> {
  res.cookies.set(sessionCookieName(), await sealSession(sessionFromTokens(tokens)), sessionCookieOptions());
  return res;
}

async function passError(r: Response): Promise<NextResponse> {
  const retryAfter = r.headers.get("retry-after");
  let payload: unknown = { error: { code: "upstream_error", message: "Request failed" } };
  try {
    payload = await r.json();
  } catch {
    // keep the generic error
  }
  return NextResponse.json(payload, {
    status: r.status,
    headers: { "Cache-Control": "no-store", ...(retryAfter ? { "Retry-After": retryAfter } : {}) },
  });
}

async function currentSession(): Promise<Session | null> {
  return openSession((await cookies()).get(sessionCookieName())?.value);
}

export async function POST(req: NextRequest, ctx: RouteContext<"/api/auth/[action]">) {
  if (!isSameOrigin(req.headers, env().appOrigin)) return jsonError(403, "csrf", "Cross-site request blocked");
  const { action } = await ctx.params;

  let raw: unknown;
  try {
    raw = await readJsonBounded(req, MAX_BODY);
  } catch (e) {
    return e instanceof BodyTooLarge
      ? jsonError(413, "payload_too_large", "Request body too large")
      : jsonError(400, "invalid_json", "Invalid request");
  }

  try {
    if (action === "logout") return await logout(req);
    if (action === "switch-tenant") return await doSwitchTenant(req, raw);

    const spec = ACTIONS[action as keyof typeof ACTIONS];
    if (!spec) return jsonError(404, "not_found", "Not found");
    const parsed = spec.body.safeParse(raw);
    // Never echo submitted values (passwords, tokens) back in validation errors.
    if (!parsed.success) return jsonError(422, "validation_error", "Invalid request");

    const r = await upstream(spec.path, { method: "POST", incoming: req.headers, json: parsed.data });
    if (!r.ok) return await passError(r);
    if (!spec.issuesSession) return NextResponse.json({ ok: true }, { headers: { "Cache-Control": "no-store" } });
    return await withSession(NextResponse.json({ ok: true }), (await r.json()) as TokenResponse);
  } catch {
    return jsonError(503, "unavailable", "The service is unavailable. Please try again.");
  }
}

async function logout(req: NextRequest): Promise<NextResponse> {
  const session = await currentSession();
  if (session) {
    // Best effort: the cookie is cleared regardless, and the API revokes the whole refresh-token family.
    await upstream("/v1/auth/logout", {
      method: "POST",
      incoming: req.headers,
      session,
      json: { refresh_token: session.refreshToken },
    }).catch(() => undefined);
  }
  const res = new NextResponse(null, { status: 204 });
  res.cookies.delete({ name: sessionCookieName(), path: "/" });
  return res;
}

async function doSwitchTenant(req: NextRequest, raw: unknown): Promise<NextResponse> {
  const session = await currentSession();
  if (!session) return jsonError(401, "unauthorized", "Not signed in");
  const parsed = switchTenant.safeParse(raw);
  if (!parsed.success) return jsonError(422, "validation_error", "Invalid request");
  const r = await upstream("/v1/auth/switch-tenant", {
    method: "POST",
    incoming: req.headers,
    session,
    json: parsed.data,
  });
  if (!r.ok) return passError(r);
  const tokens = (await r.json()) as TokenResponse;
  // The old workspace's session is no longer needed; revoke it rather than leaving it live.
  await upstream("/v1/auth/logout", {
    method: "POST",
    incoming: req.headers,
    session,
    json: { refresh_token: session.refreshToken },
  }).catch(() => undefined);
  return withSession(NextResponse.json({ ok: true }), tokens);
}
