import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Me } from "@/lib/types";

const state = vi.hoisted(() => ({
  me: {
    user_id: "owner",
    email: "admin@example.com",
    tenant: { id: "workspace", name: "Workspace", role: "admin" as const },
    permissions: ["document:read", "member:manage", "apikey:manage", "audit:read"],
    tenants: [],
    debug_enabled: false,
  } satisfies Me,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), refresh: vi.fn() }) }));
vi.mock("@/components/app-shell", () => ({
  useMe: () => state.me,
  useCan: (permission: string) => state.me.permissions.includes(permission),
}));

const { MembersView } = await import("@/components/members-view");
const { ReceiptsView } = await import("@/components/receipts-view");
const { SettingsView } = await import("@/components/settings-view");

function render(view: ComponentType, client = new QueryClient()) {
  return renderToStaticMarkup(createElement(QueryClientProvider, { client }, createElement(view)));
}

beforeEach(() => {
  state.me.permissions = ["document:read", "member:manage", "apikey:manage", "audit:read"];
});

describe("workspace views", () => {
  it("rejects direct management-page access for viewers", () => {
    state.me.permissions = ["document:read"];
    expect(render(MembersView)).toContain("permission to manage");
    expect(render(SettingsView)).toContain("Appearance");
    expect(render(SettingsView)).toContain("Profile");
    expect(render(SettingsView)).not.toContain("Create key");
  });
  it("disables own-role edits and escapes malicious member text", () => {
    const client = new QueryClient();
    client.setQueryData(
      ["members", "workspace", 0],
      [
        { user_id: "owner", email: "admin@example.com", role: "admin", is_active: true, joined_at: "2026-10-07" },
        {
          user_id: "other",
          email: "<img src=x onerror=alert(1)>",
          role: "viewer",
          is_active: true,
          joined_at: "2026-10-07",
        },
      ],
    );
    client.setQueryData(["invitations", "workspace", 0], []);
    const html = render(MembersView, client);
    expect(html).toMatch(/<select[^>]*aria-label="Role for admin@example.com"[^>]*disabled=""/);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });
  it("renders listed keys as prefixes without retrieving their secrets", () => {
    const client = new QueryClient();
    client.setQueryData(
      ["api-keys", "workspace", 0],
      [
        {
          id: "key",
          name: "Integration",
          role: "viewer",
          key_prefix: "123456789abc",
          last_used_at: null,
          revoked_at: null,
          created_at: "2026-10-07",
        },
      ],
    );
    client.setQueryData(["audit", "workspace"], { pages: [[]], pageParams: [null] });
    const html = render(SettingsView, client);
    expect(html).toContain("rk_123456789abc_…");
    expect(html).not.toContain("New API key");
    expect(html).toContain("Audit history");
  });
  it("shows an actionable empty receipt list", () => {
    const client = new QueryClient();
    client.setQueryData(["receipts", "workspace", 0], { items: [], total: 0 });
    const html = render(ReceiptsView, client);
    expect(html).toContain("No receipts");
    expect(html).toContain("Scan a receipt photo or upload a PDF here");
  });
});
