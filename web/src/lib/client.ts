/** Browser-side API client. Talks only to this app's BFF (`/api/...`); it never sees API tokens. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code = "error",
  ) {
    super(message);
  }
}

export async function errorFrom(r: Response): Promise<ApiError> {
  try {
    const body = (await r.json()) as { error?: { message?: string; code?: string } };
    if (body.error?.message) return new ApiError(r.status, body.error.message, body.error.code);
  } catch {
    // fall through
  }
  return new ApiError(r.status, r.status === 429 ? "Too many requests. Try again shortly." : "Request failed");
}

function onUnauthorized(path: string) {
  // Session expired or revoked: start over at sign-in (but not from the auth endpoints themselves).
  // A full load (not router.push) also drops every cached query from the dead session.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  if (!path.startsWith("/api/auth/") && typeof window !== "undefined") window.location.assign("/login");
}

export async function request(path: string, init: RequestInit = {}): Promise<Response> {
  let r: Response;
  try {
    r = await fetch(path, { credentials: "same-origin", cache: "no-store", ...init });
  } catch {
    throw new ApiError(503, "The service is unavailable. Please try again.");
  }
  if (r.status === 401) onUnauthorized(path);
  if (!r.ok) throw await errorFrom(r);
  return r;
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  return (await (await request(path, { signal })).json()) as T;
}

export async function sendJson<T = unknown>(method: string, path: string, body?: unknown): Promise<T | null> {
  const r = await request(path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return r.status === 204 ? null : ((await r.json()) as T);
}
