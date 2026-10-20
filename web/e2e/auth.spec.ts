import { expect, test } from "@playwright/test";
import { adminAccount, signIn, signOut } from "./helpers";

test("sign out, then sign back in", async ({ page }) => {
  const admin = adminAccount();
  await signIn(page, admin.email, admin.password);
  await expect(page).toHaveURL(/\/$/);
  await signOut(page);

  await page.goto("/documents");
  await expect(page).toHaveURL(/\/login/); // session is gone, protected pages redirect
});

test("wrong password shows an error and stays signed out", async ({ page }) => {
  await signIn(page, adminAccount().email, "not-the-right-password");
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);
});
