"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Hero } from "@/components/brand";
import { Markdown } from "@/components/markdown";
import { Alert, Button } from "@/components/ui";
import { ApiError, getJson, request, sendJson } from "@/lib/client";
import { parseSse } from "@/lib/sse";
import type { Citation, ConversationDetail, Message } from "@/lib/types";

const MAX_CHARS = 4000;
const OLDER_PAGE = 50;

type UiMessage = Message & { pending?: boolean; error?: string; local?: boolean };
type Progress = { step: string; model?: string; provider?: string };

const STEP_LABEL: Record<string, string> = {
  starting: "Starting",
  analyzing: "Understanding your question",
  retrieving: "Searching your documents",
  rewriting: "Refining the search",
  generating: "Writing the answer",
  responding: "Replying",
};
/** Distance from the bottom (px) within which the thread keeps following a streaming answer. */
const STICK_PX = 80;

export function ChatThread({ conversationId }: { conversationId: string | null }) {
  const query = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: ({ signal }) => getJson<ConversationDetail>(`/api/v1/conversations/${conversationId}`, signal),
    enabled: conversationId !== null,
    // The thread owns its state once rendered, so the query only fetches on mount. A just-created chat is
    // seeded into the cache (fresh for 5 s), so moving to its URL doesn't re-fetch it.
    staleTime: 5_000,
    refetchOnWindowFocus: false,
  });
  if (conversationId === null) return <Thread conversationId={null} initial={null} />;
  if (query.isPending || query.isFetching) return <p className="p-8 text-sm text-zinc-500">Loading conversation…</p>;
  if (query.isError) {
    return (
      <div className="p-8">
        <Alert tone="error">
          {query.error instanceof ApiError && query.error.status === 404
            ? "This conversation doesn't exist or was deleted."
            : "Could not load this conversation."}
        </Alert>
      </div>
    );
  }
  return <Thread conversationId={conversationId} initial={query.data} />;
}

function Thread({ conversationId, initial }: { conversationId: string | null; initial: ConversationDetail | null }) {
  const [messages, setMessages] = useState<UiMessage[]>(initial?.messages ?? []);
  const [hasMore, setHasMore] = useState(initial?.has_more ?? false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const qc = useQueryClient();
  const router = useRouter();
  const streaming = progress !== null;
  const lastId = messages.at(-1)?.id;

  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    stickRef.current = true;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [lastId]);
  // Follow the streamed answer unless the reader has scrolled up to read something else.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && streaming && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  async function loadOlder() {
    const oldest = messages[0];
    if (!oldest || !conversationId) return;
    const el = scrollRef.current;
    const before = el?.scrollHeight ?? 0;
    try {
      const params = new URLSearchParams({
        limit: String(OLDER_PAGE),
        before: oldest.created_at,
        before_id: oldest.id,
      });
      const page = await getJson<ConversationDetail>(`/api/v1/conversations/${conversationId}?${params}`);
      setMessages((m) => [...page.messages, ...m]);
      setHasMore(page.has_more);
      requestAnimationFrame(() => {
        if (el) el.scrollTop += el.scrollHeight - before; // keep the reader's place
      });
    } catch {
      setOlderError("Could not load earlier messages.");
    }
  }

  async function send(text: string) {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const now = new Date().toISOString();
    const base = { citations: [] as Citation[], provider: null, model: null, created_at: now, local: true };
    const userMsg: UiMessage = { ...base, id: `local-${crypto.randomUUID()}`, role: "user", content: text };
    const reply: UiMessage = {
      ...base,
      id: `local-${crypto.randomUUID()}`,
      role: "assistant",
      content: "",
      pending: true,
    };
    const replyId = reply.id;
    const show = () => setMessages((ms) => ms.map((m) => (m.id === replyId ? { ...reply } : m)));
    const before = messages;
    setMessages((ms) => [...ms, userMsg, { ...reply }]);
    setProgress({ step: "starting" });

    let done: { message_id: string; conversation_id: string } | null = null;
    try {
      const r = await request("/api/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
        signal: ctrl.signal,
      });
      if (!r.body) throw new ApiError(502, "Empty response");
      for await (const ev of parseSse(r.body)) {
        const data: unknown = JSON.parse(ev.data);
        switch (ev.event) {
          case "status":
            setProgress((p) => ({ ...p, step: (data as { step: string }).step }));
            break;
          case "model": {
            const m = data as { model: string; provider: string };
            reply.model = m.model;
            reply.provider = m.provider;
            setProgress((p) => ({ step: p?.step ?? "generating", model: m.model, provider: m.provider }));
            show();
            break;
          }
          case "token":
            reply.content += (data as { text: string }).text;
            show();
            break;
          case "answer": // canonical text after citation validation; replaces the provisional stream
            reply.content = (data as { text: string }).text;
            show();
            break;
          case "citations":
            reply.citations = data as Citation[];
            show();
            break;
          case "error":
            reply.error = (data as { message: string }).message;
            show();
            break;
          case "done":
            done = data as { message_id: string; conversation_id: string };
            break;
        }
      }
    } catch (e) {
      reply.error = ctrl.signal.aborted
        ? "Stopped."
        : e instanceof ApiError
          ? e.message
          : "Connection lost. Try again.";
    }
    reply.pending = false;
    if (done) {
      reply.id = done.message_id;
      reply.local = false;
    }
    setMessages((ms) => ms.map((m) => (m.id === replyId ? { ...reply } : m)));
    setProgress(null);
    abortRef.current = null;
    void qc.invalidateQueries({ queryKey: ["conversations"] });

    if (done && conversationId === null) {
      // First turn of a new chat: move to its permanent URL without re-fetching what we already have.
      const detail: ConversationDetail = {
        id: done.conversation_id,
        title: text.slice(0, 80),
        created_at: now,
        updated_at: new Date().toISOString(),
        messages: [...before, userMsg, { ...reply }],
        has_more: false,
      };
      qc.setQueryData(["conversation", done.conversation_id], detail);
      router.replace(`/c/${done.conversation_id}`);
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
        }}
        className="min-h-0 flex-1 overflow-y-auto"
      >
        <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
          {hasMore && (
            <div className="text-center">
              <Button variant="ghost" onClick={() => void loadOlder()}>
                Load earlier messages
              </Button>
              {olderError && <p className="text-sm text-red-600">{olderError}</p>}
            </div>
          )}
          {messages.length === 0 && <EmptyState />}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end">
                <p className="max-w-[85%] rounded-2xl bg-zinc-100 px-4 py-2.5 whitespace-pre-wrap dark:bg-zinc-800">
                  {m.content}
                </p>
              </div>
            ) : (
              <AssistantMessage key={m.id} m={m} progress={m.pending ? progress : null} />
            ),
          )}
        </div>
      </div>
      <Composer
        disabled={streaming}
        onSend={(t) => void send(t)}
        onStop={() => abortRef.current?.abort()}
        placeholder={conversationId ? "Continue this conversation…" : "Ask a question about your documents…"}
      />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="space-y-6 pt-4 text-center sm:pt-[8vh]">
      <Hero />
      <h2 className="text-xl font-semibold">Ask your documents</h2>
      <p className="mt-2 text-sm text-zinc-500">
        Answers are grounded in your workspace&apos;s documents and cite their sources. Earlier chats are in the
        sidebar; open one to continue it.
      </p>
    </div>
  );
}

function AssistantMessage({ m, progress }: { m: UiMessage; progress: Progress | null }) {
  return (
    <div className="space-y-2">
      {progress && (
        <p className="animate-pulse text-xs text-zinc-500" aria-live="polite">
          {STEP_LABEL[progress.step] ?? progress.step}…
          {progress.model ? ` using ${progress.model} (${progress.provider})` : ""}
        </p>
      )}
      {m.content && <Markdown>{m.content}</Markdown>}
      {m.error && <Alert tone="error">{m.error}</Alert>}
      {m.citations.length > 0 && <Sources citations={m.citations} />}
      {!m.pending && (
        <div className="flex items-center gap-3 text-xs text-zinc-500">
          {m.model && (
            <span>
              {m.model} · {m.provider}
            </span>
          )}
          {m.content && <CopyButton text={m.content} />}
          {!m.local && <FeedbackButtons messageId={m.id} />}
        </div>
      )}
    </div>
  );
}

function Sources({ citations }: { citations: Citation[] }) {
  return (
    <details className="rounded-lg border border-zinc-200 text-sm dark:border-zinc-800">
      <summary className="cursor-pointer px-3 py-2 text-zinc-600 select-none dark:text-zinc-400">
        Sources ({citations.length})
      </summary>
      <ol className="space-y-2 px-3 pb-3">
        {citations.map((c) => (
          <li key={c.n}>
            <p className="font-medium">
              [{c.n}] {c.source}
              {c.page ? `, page ${c.page}` : ""}
            </p>
            <p className="text-zinc-500">{c.snippet}</p>
          </li>
        ))}
      </ol>
    </details>
  );
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  }
  return (
    <button
      onClick={() => void copy()}
      aria-label="Copy answer"
      className="rounded px-1.5 py-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800"
    >
      <span aria-live="polite">{state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}</span>
    </button>
  );
}

function FeedbackButtons({ messageId }: { messageId: string }) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [failed, setFailed] = useState(false);
  async function rate(value: 1 | -1) {
    setFailed(false);
    try {
      await sendJson("POST", `/api/v1/messages/${messageId}/feedback`, { rating: value });
      setRating(value);
    } catch {
      setFailed(true);
    }
  }
  return (
    <span className="flex items-center gap-1">
      {([1, -1] as const).map((v) => (
        <button
          key={v}
          aria-label={v === 1 ? "Helpful" : "Not helpful"}
          aria-pressed={rating === v}
          onClick={() => void rate(v)}
          className={`rounded px-1.5 py-0.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${rating === v ? "text-indigo-600 dark:text-indigo-400" : ""}`}
        >
          {v === 1 ? "👍" : "👎"}
        </button>
      ))}
      {failed && <span className="text-red-600">Couldn&apos;t save</span>}
    </span>
  );
}

function Composer({
  disabled,
  onSend,
  onStop,
  placeholder,
}: {
  disabled: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  placeholder: string;
}) {
  const [text, setText] = useState("");
  const trimmed = text.trim();

  function submit() {
    if (!trimmed || disabled || trimmed.length > MAX_CHARS) return;
    onSend(trimmed);
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="border-t border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <form
        className="mx-auto flex max-w-3xl items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="sr-only" htmlFor="composer">
          Message
        </label>
        <textarea
          id="composer"
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={Math.min(8, Math.max(1, text.split("\n").length))}
          maxLength={MAX_CHARS}
          placeholder={placeholder}
          className="min-h-11 flex-1 resize-none rounded-xl border border-zinc-300 bg-white px-3 py-2.5 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 dark:border-zinc-700 dark:bg-zinc-900"
        />
        {disabled ? (
          <Button type="button" variant="secondary" onClick={onStop} className="h-11">
            Stop
          </Button>
        ) : (
          <Button type="submit" disabled={!trimmed} className="h-11">
            Send
          </Button>
        )}
      </form>
      <p className="mx-auto mt-1 max-w-3xl text-right text-xs text-zinc-400">
        {text.length > MAX_CHARS - 500 ? `${text.length}/${MAX_CHARS}` : "Enter to send · Shift+Enter for a new line"}
      </p>
    </div>
  );
}
