import { describe, expect, it } from "vitest";
import { navigationFor } from "@/lib/navigation";

describe("workspace navigation", () => {
  it("shows all documented options for administrators", () => {
    const nav = navigationFor(
      ["chat:use", "document:read", "member:manage", "apikey:manage", "audit:read"],
      "/members",
    );
    expect(nav.map((item) => item.label)).toEqual(["Home", "Upload docs", "Scan receipts", "Members", "Settings"]);
    expect(nav.filter((item) => item.active).map((item) => item.label)).toEqual(["Members"]);
  });
  it("hides management options from viewers and editors", () => {
    expect(
      navigationFor(["chat:use", "document:read", "document:write"], "/receipts").map((item) => item.label),
    ).toEqual(["Home", "Upload docs", "Scan receipts", "Settings"]);
  });
  it("supports an audit-only settings permission", () => {
    expect(navigationFor(["audit:read"], "/settings").map((item) => item.label)).toEqual(["Settings"]);
  });
});
