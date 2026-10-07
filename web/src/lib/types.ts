export type Role = "admin" | "editor" | "viewer";

export type Tenant = { id: string; name: string; role: Role };

export type Me = {
  user_id: string | null;
  email: string | null;
  tenant: Tenant;
  permissions: string[];
  tenants: Tenant[];
  debug_enabled: boolean;
};

export type Citation = { n: number; doc_id: string; source: string; page: number | null; snippet: string };

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  provider: string | null;
  model: string | null;
  created_at: string;
};

export type Conversation = { id: string; title: string; created_at: string; updated_at: string };

export type ConversationDetail = Conversation & { messages: Message[]; has_more: boolean };

export type DocumentStatus = "queued" | "processing" | "ready" | "failed" | "deleted";

export type DocumentItem = {
  id: string;
  title: string;
  mime_type: string;
  size_bytes: number;
  version: number;
  status: DocumentStatus;
  error: string | null;
  progress: { step?: string; provider?: string; model?: string } | null;
  chunk_count: number;
  created_at: string;
  updated_at: string;
};

export type Page<T> = { items: T[]; total: number };
