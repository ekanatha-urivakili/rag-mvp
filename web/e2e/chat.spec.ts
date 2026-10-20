import { expect, test } from "@playwright/test";
import { ADMIN_STATE } from "./helpers";

// Needs a working model route (local Ollama or a cloud key), so it is opt-in: E2E_CHAT=1.
test.skip(!process.env.E2E_CHAT, "set E2E_CHAT=1 when a model is available");
test.use({ storageState: ADMIN_STATE });
test.setTimeout(180_000);

test("a new chat streams an answer, moves to /c/{id}, and appears in history", async ({ page }) => {
  const message = `Hello! What can you help me with? (${crypto.randomUUID().slice(0, 8)})`;
  await page.goto("/");
  const composer = page.getByRole("textbox").last();
  await composer.fill(message);
  await composer.press("Enter");

  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/, { timeout: 120_000 });
  const path = new URL(page.url()).pathname;
  const history = page.getByRole("navigation", { name: "Chat history" });
  await expect(history.locator(`a[href="${path}"]`)).toHaveText(message);

  await page.reload(); // the conversation is persisted, not just client state
  await expect(page.getByText(message).first()).toBeVisible();
  expect(new URL(page.url()).pathname).toBe(path);
});
