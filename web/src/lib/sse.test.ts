import { describe, expect, it } from "vitest";
import { parseSse } from "@/lib/sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(c) {
      for (const ch of chunks) c.enqueue(new TextEncoder().encode(ch));
      c.close();
    },
  });
}

async function collect(s: ReadableStream<Uint8Array>) {
  const out = [];
  for await (const e of parseSse(s)) out.push(e);
  return out;
}

describe("parseSse", () => {
  it("handles events split across chunks and CRLF", async () => {
    const events = await collect(
      streamOf('event: status\r\ndata: {"step":', '"retrieving"}\r\n\r\nevent: tok', 'en\ndata: {"text":"Hi"}\n\n'),
    );
    expect(events).toEqual([
      { event: "status", data: '{"step":"retrieving"}' },
      { event: "token", data: '{"text":"Hi"}' },
    ]);
  });

  it("emits a trailing event without the final blank line", async () => {
    expect(await collect(streamOf('event: done\ndata: {"ok":1}'))).toEqual([{ event: "done", data: '{"ok":1}' }]);
  });
});
