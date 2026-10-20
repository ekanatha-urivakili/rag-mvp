import { expect, test, type Browser } from "@playwright/test";
import { ADMIN_STATE, emailLink, newPassword, uniqueEmail } from "./helpers";

test.use({ storageState: ADMIN_STATE });

async function acceptInvite(browser: Browser, link: string, password: string) {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await page.goto(link);
  await expect(page).toHaveURL(/\/accept_invite$/);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Accept invitation" }).click();
  await expect(page).toHaveURL(/\/$/);
  return { context, page };
}

test("admin invites a viewer, who joins with role-limited navigation", async ({ page, request, browser }) => {
  const email = uniqueEmail("viewer");
  await page.goto("/members");
  await page.getByLabel("Email address").fill(email);
  await page.locator('form select[name="role"]').selectOption("viewer");
  await page.getByRole("button", { name: "Send invitation" }).click();
  await expect(page.getByText("Invitation sent.")).toBeVisible();
  await expect(page.getByText(email)).toBeVisible(); // listed as pending

  const viewer = await acceptInvite(browser, await emailLink(request, email, "accept_invite"), newPassword());
  const nav = viewer.page.getByRole("navigation", { name: "Workspace" });
  await expect(nav).toContainText("Upload docs");
  await expect(nav).toContainText("Scan receipts");
  await expect(nav).not.toContainText("Members");

  await viewer.page.goto("/members");
  await expect(viewer.page.getByText("You don't have permission to manage workspace members.")).toBeVisible();
  await viewer.context.close();

  await page.reload();
  await expect(page.getByLabel(`Role for ${email}`)).toHaveValue("viewer");
});

test("admin creates an API key, sees the secret once, and revokes it", async ({ page }) => {
  const name = `e2e-key-${crypto.randomUUID().slice(0, 6)}`;
  await page.goto("/settings");
  await page.getByLabel("Key name").fill(name);
  await page.getByRole("button", { name: "Create key" }).click();

  await expect(page.getByText(`Copy ${name} now. This secret is shown only once.`)).toBeVisible();
  await expect(page.getByLabel("New API key")).toHaveValue(/^rk_[A-Za-z0-9]+_/);
  await page.getByRole("button", { name: "Dismiss secret" }).click();
  await expect(page.getByLabel("New API key")).toHaveCount(0);

  const row = page.getByRole("listitem").filter({ hasText: name });
  await row.getByRole("button", { name: "Revoke" }).click();
  await row.getByRole("button", { name: "Revoke" }).click(); // confirm
  await expect(row).toContainText("revoked");
});
