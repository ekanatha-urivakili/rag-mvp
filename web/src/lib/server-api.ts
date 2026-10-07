import "server-only";

import { cookies, headers } from "next/headers";
import { openSession, sessionCookieName } from "@/lib/session";
import type { Me } from "@/lib/types";
import { upstream } from "@/lib/upstream";

/** Loads the signed-in user for server components. null = not signed in or session revoked. */
export async function getMe(): Promise<Me | null> {
  const session = await openSession((await cookies()).get(sessionCookieName())?.value);
  if (!session) return null;
  const r = await upstream("/v1/me", { method: "GET", incoming: await headers(), session });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`GET /v1/me failed with ${r.status}`);
  return (await r.json()) as Me;
}
