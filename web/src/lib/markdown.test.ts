import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Markdown } from "@/components/markdown";

describe("model output rendering", () => {
  it("drops raw HTML, remote images and javascript links", () => {
    const output = renderToStaticMarkup(
      createElement(
        Markdown,
        null,
        '<script>alert(1)</script>\n\n<img src="https://attacker.example/leak">\n\n' +
          "![leak](https://attacker.example/leak)\n\n[click](javascript:alert%281%29)",
      ),
    );
    expect(output).not.toContain("<script");
    expect(output).not.toContain("<img");
    expect(output).not.toContain("attacker.example");
    expect(output).not.toContain("javascript:");
  });
});
