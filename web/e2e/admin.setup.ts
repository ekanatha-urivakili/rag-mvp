import { mkdirSync, writeFileSync } from "node:fs";
import { expect, test as setup } from "@playwright/test";
import { ADMIN_ACCOUNT, ADMIN_STATE, AUTH_DIR, emailLink, newPassword, uniqueEmail, type Account } from "./helpers";

// Self-serve signup: email → verification link from Mailpit → private workspace as admin.
setup("sign up and verify a new workspace admin", async ({ page, request }) => {
  const account: Account = {
    email: uniqueEmail("admin"),
    password: newPassword(),
    workspace: `E2E ${crypto.randomUUID().slice(0, 8)}`,
  };

  await page.goto("/login");
  await page.getByRole("tab", { name: "Sign up" }).click();
  await page.getByLabel("Email", { exact: true }).fill(account.email);
  await page.getByRole("button", { name: "Send verification link" }).click();
  await expect(page.getByRole("status")).toContainText("verification link is on its way");

  await page.goto(await emailLink(request, account.email, "verify_signup"));
  await expect(page).toHaveURL(/\/verify_signup$/); // token stripped from the address bar
  await page.getByLabel("Workspace name").fill(account.workspace);
  await page.getByLabel("Password (12–128 characters)").fill(account.password);
  await page.getByLabel("Repeat password").fill(account.password);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("navigation", { name: "Workspace" })).toContainText("Members");

  mkdirSync(AUTH_DIR, { recursive: true });
  writeFileSync(ADMIN_ACCOUNT, JSON.stringify(account));
  await page.context().storageState({ path: ADMIN_STATE });
});
