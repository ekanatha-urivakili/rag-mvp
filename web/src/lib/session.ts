import "server-only";

import { EncryptJWT, jwtDecrypt } from "jose";
import { env } from "@/lib/env";

/**
 * API tokens live only here: an encrypted (A256GCM), HttpOnly, SameSite=Strict cookie.
 * JavaScript in the browser can never read them (OWASP A07, XSS token theft).
 */
export type Session = {
  accessToken: string;
  refreshToken: string;
  accessExpiresAt: number; // epoch seconds
};

export type TokenResponse = { access_token: string; refresh_token: string; expires_in: number };

export const SESSION_TTL_S = 14 * 24 * 3600; // matches the API refresh-token TTL
const REFRESH_LEEWAY_S = 60;

export function sessionCookieName(): string {
  // __Host- forbids Domain and requires Secure + Path=/, so subdomains can't plant or read it.
  return env().secureCookies ? "__Host-rag_session" : "rag_session";
}

export function sessionCookieOptions() {
  return {
    httpOnly: true,
    secure: env().secureCookies,
    sameSite: "strict" as const,
    path: "/",
    maxAge: SESSION_TTL_S,
  };
}

let keyCache: { secret: string; key: Uint8Array } | undefined;

async function key(): Promise<Uint8Array> {
  const secret = env().SESSION_SECRET;
  if (keyCache?.secret !== secret) {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(secret));
    keyCache = { secret, key: new Uint8Array(digest) };
  }
  return keyCache.key;
}

export function sessionFromTokens(t: TokenResponse): Session {
  return {
    accessToken: t.access_token,
    refreshToken: t.refresh_token,
    accessExpiresAt: Math.floor(Date.now() / 1000) + t.expires_in,
  };
}

export async function sealSession(s: Session): Promise<string> {
  return new EncryptJWT({ at: s.accessToken, rt: s.refreshToken, aexp: s.accessExpiresAt })
    .setProtectedHeader({ alg: "dir", enc: "A256GCM" })
    .setIssuedAt()
    .setExpirationTime(`${SESSION_TTL_S}s`)
    .encrypt(await key());
}

export async function openSession(sealed: string | undefined): Promise<Session | null> {
  if (!sealed || sealed.length > 4096) return null;
  try {
    const { payload } = await jwtDecrypt(sealed, await key(), {
      keyManagementAlgorithms: ["dir"],
      contentEncryptionAlgorithms: ["A256GCM"],
    });
    const { at, rt, aexp } = payload;
    if (typeof at !== "string" || typeof rt !== "string" || typeof aexp !== "number") return null;
    return { accessToken: at, refreshToken: rt, accessExpiresAt: aexp };
  } catch {
    return null; // tampered, expired, or sealed with a rotated secret
  }
}

export function needsRefresh(s: Session, nowS = Math.floor(Date.now() / 1000)): boolean {
  return s.accessExpiresAt - nowS < REFRESH_LEEWAY_S;
}
