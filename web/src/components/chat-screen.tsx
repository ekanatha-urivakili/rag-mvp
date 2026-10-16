"use client";

import { ChatThread } from "@/components/chat-thread";

export function ChatScreen({ conversationId }: { conversationId: string | null }) {
  return <ChatThread key={conversationId ?? "new"} conversationId={conversationId} />;
}
