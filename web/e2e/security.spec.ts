import { expect, test } from "@playwright/test";

test("protected pages redirect to sign-in without a session", async ({ page }) => {
  for (const path of ["/", "/documents", "/members", "/settings"]) {
    await page.goto(path);
    await expect(page).toHaveURL(/\/login/);
  }
});

test("pages send a nonce CSP and hardening headers", async ({ request }) => {
  const res = await request.get("/login");
  const headers = res.headers();
  expect(headers["content-security-policy"]).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic'/);
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["content-security-policy"]).toContain("img-src 'self' blob: data:");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("BFF rejects cross-site mutations (CSRF)", async ({ request }) => {
  const res = await request.post("/api/auth/login", {
    headers: { Origin: "https://evil.example" },
    data: { email: "someone@example.com", password: "irrelevant-password" },
  });
  expect(res.status()).toBe(403);
});

test("BFF proxies only allowlisted API paths", async ({ request }) => {
  // Traversal is folded into a page path (redirected to sign-in); encoded slashes and unlisted paths are 404s.
  for (const path of ["/api/v1/%2e%2e/%2e%2e/healthz", "/api/v1/documents%2F..%2Fme", "/api/v1/openapi.json"]) {
    const res = await request.get(path, { maxRedirects: 0 });
    expect([307, 404], path).toContain(res.status());
    expect(await res.text(), path).not.toContain('"status":"ok"');
  }
});
