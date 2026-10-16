import { describe, expect, it } from "vitest";
import { navigationFor } from "@/lib/navigation";

describe("workspace navigation", () => {
  it("shows all documented options for administrators", () => {
    const nav = navigationFor(
      ["chat:use", "document:read", "member:manage", "apikey:manage", "audit:read"],
      "/members",
    );
    expect(nav.map((item) => item.label)).toEqual([
      "Home",
      "Upload docs",
      "Scan receipts",
      "Usage",
      "Members",
      "Settings",
    ]);
    expect(nav.filter((item) => item.active).map((item) => item.label)).toEqual(["Members"]);
  });
  it("hides management options from viewers and editors", () => {
    expect(
      navigationFor(["chat:use", "document:read", "document:write"], "/receipts").map((item) => item.label),
    ).toEqual(["Home", "Upload docs", "Scan receipts", "Usage", "Settings"]);
  });
  it("marks usage active and hides it without document access", () => {
    expect(navigationFor(["document:read"], "/usage").find((item) => item.href === "/usage")?.active).toBe(true);
    expect(navigationFor(["chat:use"], "/usage").some((item) => item.href === "/usage")).toBe(false);
  });
  it("supports an audit-only settings permission", () => {
    expect(navigationFor(["audit:read"], "/settings").map((item) => item.label)).toEqual(["Settings"]);
  });
});
