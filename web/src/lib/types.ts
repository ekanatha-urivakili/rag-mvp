export type Role = "admin" | "editor" | "viewer";

export type Tenant = { id: string; name: string; role: Role };

export type Me = {
  user_id: string | null;
  email: string | null;
  name?: string | null;
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

export type Member = { user_id: string; email: string; role: Role; is_active: boolean; joined_at: string };
export type Invitation = { id: string; email: string; role: Role; expires_at: string; created_at: string };
export type ApiKey = {
  id: string;
  name: string;
  role: Role;
  key_prefix: string;
  last_used_at: string | null;
  revoked_at: string | null;
  created_at: string;
};
export type CreatedApiKey = ApiKey & { api_key: string };
export type AuditEvent = {
  id: number;
  actor_user_id: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  metadata: Record<string, unknown>;
  ip: string | null;
  created_at: string;
};
export type Receipt = {
  document_id: string;
  title: string;
  merchant_name: string | null;
  merchant_address: string | null;
  merchant_phone: string | null;
  purchased_on: string | null;
  purchased_time: string | null;
  currency: string | null;
  item_count: number | null;
  subtotal: string | null;
  discount_total: string | null;
  tax: string | null;
  tip: string | null;
  total: string | null;
  payment_method: string | null;
  card_brand: string | null;
  card_last4: string | null;
  warnings: string[];
  provider: string;
  model: string;
  created_at: string;
};
export type ReceiptDetail = Receipt & {
  items: { description: string; quantity: string | null; unit_price: string | null; amount: string }[];
  discounts: { description: string; amount: string }[];
};
