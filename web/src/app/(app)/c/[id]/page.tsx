import { notFound } from "next/navigation";
import { ChatScreen } from "@/components/chat-screen";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Deep link to a past conversation; sending a message here continues it. */
export default async function ConversationPage({ params }: PageProps<"/c/[id]">) {
  const { id } = await params;
  if (!UUID.test(id)) notFound();
  return <ChatScreen conversationId={id.toLowerCase()} />;
}
