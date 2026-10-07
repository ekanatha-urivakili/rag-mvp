"use client";

import { useState } from "react";
import { ChatThread } from "@/components/chat-thread";
import { HistorySidebar } from "@/components/history-sidebar";

export function ChatScreen({ conversationId }: { conversationId: string | null }) {
  const [historyOpen, setHistoryOpen] = useState(false);
  return (
    <div className="relative flex h-full">
      <div
        className={`${historyOpen ? "absolute inset-y-0 left-0 z-10 flex shadow-xl" : "hidden"} md:static md:flex md:shadow-none`}
      >
        <HistorySidebar activeId={conversationId} onNavigate={() => setHistoryOpen(false)} />
      </div>
      <main className="min-w-0 flex-1">
        <button
          onClick={() => setHistoryOpen((o) => !o)}
          aria-expanded={historyOpen}
          className="m-2 rounded-md border border-zinc-300 px-2 py-1 text-xs md:hidden dark:border-zinc-700"
        >
          {historyOpen ? "Close history" : "History"}
        </button>
        <ChatThread key={conversationId ?? "new"} conversationId={conversationId} />
      </main>
    </div>
  );
}
