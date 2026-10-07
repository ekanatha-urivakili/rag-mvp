import { describe, expect, it } from "vitest";
import { historyGroup } from "@/lib/format";

describe("historyGroup", () => {
  const now = new Date(2026, 9, 7, 15, 0);
  it.each([
    [new Date(2026, 9, 7, 0, 5), "Today"],
    [new Date(2026, 9, 6, 23, 0), "Yesterday"],
    [new Date(2026, 9, 2), "Previous 7 days"],
    [new Date(2026, 8, 1), "Older"],
  ])("%s → %s", (d, group) => expect(historyGroup(d.toISOString(), now)).toBe(group));
});
