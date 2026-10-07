import { describe, expect, it } from "vitest";
import { uploadError } from "@/lib/uploads";

describe("upload formats", () => {
  it("keeps receipt and document formats separate", () => {
    expect(uploadError({ name: "receipt.JPG", size: 10 }, "receipt")).toBeNull();
    expect(uploadError({ name: "receipt.JPG", size: 10 }, "document")).toContain("document");
    expect(uploadError({ name: "notes.txt", size: 10 }, "receipt")).toContain("receipt");
    expect(uploadError({ name: "report.pdf.exe", size: 10 }, "document")).toContain("document");
    expect(uploadError({ name: "report.PDF", size: 10 }, "document")).toBeNull();
  });
  it("rejects empty, oversized and HEIC files", () => {
    expect(uploadError({ name: "a.pdf", size: 0 }, "document")).toContain("empty");
    expect(uploadError({ name: "a.png", size: 25 * 1024 * 1024 + 1 }, "receipt")).toContain("25 MB");
    expect(uploadError({ name: "a.heic", size: 10 }, "receipt")).toContain("receipt");
  });
});
