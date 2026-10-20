import { defineConfig, devices } from "@playwright/test";

// Browser end-to-end tests against a running stack (web + api + worker + mailpit).
// Local: `./start.sh`, then `npm run e2e`. CI starts the stack itself (see .github/workflows/ci.yml).
export default defineConfig({
  testDir: "e2e",
  // Specs share one admin workspace created by the setup project; signup is rate-limited per IP.
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    ...devices["Desktop Chrome"],
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /admin\.setup\.ts/ },
    { name: "e2e", testMatch: /.*\.spec\.ts/, dependencies: ["setup"] },
  ],
});
