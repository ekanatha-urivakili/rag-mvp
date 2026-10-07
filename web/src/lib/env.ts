import "server-only";

import { z } from "zod";

const schema = z
  .object({
    // FastAPI base URL. Server-side only: the browser never talks to the API directly.
    API_URL: z.url().default("http://localhost:8000"),
    // Public origin of this app (scheme://host[:port]). Mutating requests must come from it (CSRF).
    APP_ORIGIN: z.url().default("http://localhost:3000"),
    // Encrypts the session cookie (A256GCM). Rotate to log everyone out.
    SESSION_SECRET: z.string().min(32, "SESSION_SECRET must be at least 32 characters"),
    // Number of trusted reverse proxies in front of this app that append to X-Forwarded-For.
    // Next.js itself sets X-Forwarded-For to the socket peer when absent, so 1 = "no ingress".
    TRUSTED_PROXY_HOPS: z.coerce.number().int().min(1).max(5).default(1),
    ENV: z.enum(["dev", "test", "prod"]).default("dev"),
  })
  .refine((e) => e.ENV !== "prod" || e.APP_ORIGIN.startsWith("https://"), {
    message: "APP_ORIGIN must be https in prod",
  });

export type Env = z.infer<typeof schema> & { secureCookies: boolean; appOrigin: string };

let cached: Env | undefined;

/** Validated lazily so `next build` doesn't need runtime secrets. */
export function env(): Env {
  if (!cached) {
    const parsed = schema.parse(process.env);
    const origin = new URL(parsed.APP_ORIGIN).origin;
    cached = { ...parsed, appOrigin: origin, secureCookies: origin.startsWith("https://") };
  }
  return cached;
}
