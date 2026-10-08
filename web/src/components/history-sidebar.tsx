"use client";

import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, getJson, sendJson } from "@/lib/client";
import { historyGroup, type HistoryGroup } from "@/lib/format";
import type { Conversation } from "@/lib/types";

const PAGE = 30;
type Cursor = { before: string; id: string };

function useDebounced(value: string, ms: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function HistorySidebar({ activeId, onNavigate }: { activeId: string | null; onNavigate?: () => void }) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim().slice(0, 100), 250);
  const history = useInfiniteQuery({
    queryKey: ["conversations", q],
    initialPageParam: null as Cursor | null,
    queryFn: ({ pageParam, signal }) => {
      const p = new URLSearchParams({ limit: String(PAGE) });
      if (q) p.set("q", q);
      if (pageParam) {
        p.set("before", pageParam.before);
        p.set("before_id", pageParam.id);
      }
      return getJson<Conversation[]>(`/api/v1/conversations?${p}`, signal);
    },
    // Keyset cursor (updated_at, id): stable while chats are continued or deleted between page loads.
    getNextPageParam: (last) => {
      const tail = last.at(-1);
      return last.length === PAGE && tail ? { before: tail.updated_at, id: tail.id } : undefined;
    },
  });

  const items = history.data?.pages.flat() ?? [];
  const groups = new Map<HistoryGroup, Conversation[]>();
  for (const c of items) {
    const g = historyGroup(c.updated_at);
    groups.set(g, [...(groups.get(g) ?? []), c]);
  }

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/40">
      <div className="space-y-2 p-3">
        <Link
          href="/"
          onClick={onNavigate}
          className="flex w-full items-center justify-center rounded-lg bg-zinc-900 px-3 py-2 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-100 dark:text-zinc-900"
        >
          + New chat
        </Link>
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          maxLength={100}
          placeholder="Search chats"
          aria-label="Search chats"
          className="w-full rounded-lg border border-zinc-300 bg-white px-3 py-1.5 text-sm outline-none focus:border-indigo-500 dark:border-zinc-700 dark:bg-zinc-950"
        />
      </div>
      <nav aria-label="Chat history" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {history.isPending && <p className="px-2 text-sm text-zinc-500">Loading…</p>}
        {history.isError && <p className="px-2 text-sm text-red-600">Could not load chats.</p>}
        {history.isSuccess && items.length === 0 && (
          <p className="px-2 text-sm text-zinc-500">{q ? "No chats match." : "No chats yet."}</p>
        )}
        {[...groups].map(([group, convs]) => (
          <section key={group} className="mt-3">
            <h2 className="px-2 pb-1 text-xs font-medium text-zinc-500">{group}</h2>
            <ul>
              {convs.map((c) => (
                <HistoryItem key={c.id} c={c} active={c.id === activeId} onNavigate={onNavigate} />
              ))}
            </ul>
          </section>
        ))}
        {history.hasNextPage && (
          <button
            onClick={() => void history.fetchNextPage()}
            disabled={history.isFetchingNextPage}
            className="mt-2 w-full rounded-md py-1.5 text-sm text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            {history.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        )}
      </nav>
    </aside>
  );
}

function HistoryItem({ c, active, onNavigate }: { c: Conversation; active: boolean; onNavigate?: () => void }) {
  const [mode, setMode] = useState<"view" | "rename" | "confirm-delete">("view");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const router = useRouter();

  async function rename(title: string) {
    const t = title.trim();
    if (!t || t === c.title) return setMode("view");
    try {
      await sendJson("PATCH", `/api/v1/conversations/${c.id}`, { title: t.slice(0, 200) });
      setMode("view");
      await qc.invalidateQueries({ queryKey: ["conversations"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Rename failed");
    }
  }

  async function remove() {
    try {
      await sendJson("DELETE", `/api/v1/conversations/${c.id}`);
      qc.removeQueries({ queryKey: ["conversation", c.id] });
      await qc.invalidateQueries({ queryKey: ["conversations"] });
      if (active) router.push("/");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Delete failed");
      setMode("view");
    }
  }

  if (mode === "rename") {
    return (
      <li className="px-1 py-0.5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void rename(String(new FormData(e.currentTarget).get("title") ?? ""));
          }}
        >
          <input
            name="title"
            defaultValue={c.title}
            maxLength={200}
            autoFocus
            aria-label="Chat title"
            onBlur={(e) => void rename(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setMode("view")}
            className="w-full rounded-md border border-indigo-500 bg-white px-2 py-1 text-sm outline-none dark:bg-zinc-950"
          />
        </form>
        {error && <p className="px-2 text-xs text-red-600">{error}</p>}
      </li>
    );
  }

  return (
    <li className="group relative">
      <Link
        href={`/c/${c.id}`}
        onClick={onNavigate}
        aria-current={active ? "page" : undefined}
        title={c.title}
        className={`block truncate rounded-md py-1.5 pr-16 pl-2 text-sm ${active ? "bg-zinc-200 font-medium dark:bg-zinc-800" : "hover:bg-zinc-100 dark:hover:bg-zinc-800/60"}`}
      >
        {c.title}
      </Link>
      <div
        className={`absolute top-1 right-1 flex gap-0.5 text-xs ${mode === "confirm-delete" ? "" : "opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 [@media(hover:none)]:opacity-100"}`}
      >
        {mode === "confirm-delete" ? (
          <>
            <button onClick={() => void remove()} className="rounded bg-red-600 px-1.5 py-0.5 text-white">
              Delete
            </button>
            <button
              onClick={() => setMode("view")}
              className="rounded px-1.5 py-0.5 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              aria-label={`Rename "${c.title}"`}
              onClick={() => {
                setError(null);
                setMode("rename");
              }}
              className="rounded px-1.5 py-0.5 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            >
              ✎
            </button>
            <button
              aria-label={`Delete "${c.title}"`}
              onClick={() => setMode("confirm-delete")}
              className="rounded px-1.5 py-0.5 hover:bg-zinc-200 dark:hover:bg-zinc-700"
            >
              ✕
            </button>
          </>
        )}
      </div>
      {error && <p className="px-2 text-xs text-red-600">{error}</p>}
    </li>
  );
}
