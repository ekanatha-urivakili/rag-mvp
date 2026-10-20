import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, type APIRequestContext, type Page } from "@playwright/test";

const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://127.0.0.1:8025";
export const AUTH_DIR = path.join(import.meta.dirname, ".auth");
export const ADMIN_STATE = path.join(AUTH_DIR, "admin.json");
export const ADMIN_ACCOUNT = path.join(AUTH_DIR, "admin-account.json");

export type Account = { email: string; password: string; workspace: string };

type MailpitSearch = { messages: { ID: string }[] };
type MailpitMessage = { Text: string };

export function uniqueEmail(kind: string): string {
  return `e2e-${kind}-${crypto.randomUUID().slice(0, 8)}@example.com`;
}

export function newPassword(): string {
  return `${crypto.randomUUID()}-${crypto.randomUUID()}`.slice(0, 40);
}

export function adminAccount(): Account {
  return JSON.parse(readFileSync(ADMIN_ACCOUNT, "utf8")) as Account;
}

/** Waits for the newest email to `to` and returns its `/<page>?token=…` link as a path. */
export async function emailLink(request: APIRequestContext, to: string, page: string): Promise<string> {
  const pattern = new RegExp(`/${page}\\?token=[A-Za-z0-9_-]+`);
  let link = "";
  await expect
    .poll(
      async () => {
        const search = await request.get(`${MAILPIT}/api/v1/search`, { params: { query: `to:"${to}"` } });
        const { messages } = (await search.json()) as MailpitSearch;
        const newest = messages[0];
        if (!newest) return "";
        const message = await request.get(`${MAILPIT}/api/v1/message/${newest.ID}`);
        link = ((await message.json()) as MailpitMessage).Text.match(pattern)?.[0] ?? "";
        return link;
      },
      { message: `email with a ${page} link to ${to}`, timeout: 30_000 },
    )
    .not.toBe("");
  return link;
}

export async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export async function signOut(page: Page): Promise<void> {
  // The shell renders the sidebar twice (mobile drawer and desktop); use the visible one.
  await page.locator("aside details summary").filter({ visible: true }).click();
  await page.getByRole("button", { name: "Sign out" }).filter({ visible: true }).click();
  await expect(page).toHaveURL(/\/login$/);
}
