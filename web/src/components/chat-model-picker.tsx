"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Alert, Button, Field } from "@/components/ui";
import { ApiError, sendJson } from "@/lib/client";
import type { ChatModel, ChatModels } from "@/lib/types";

const INPUT =
  "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950";

export function ChatModelPicker({
  catalog,
  value,
  onChange,
  disabled,
  loading,
  failed,
}: {
  catalog: ChatModels | undefined;
  value: string;
  onChange: (id: string) => void;
  disabled: boolean;
  loading: boolean;
  failed: boolean;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<ChatModel["provider"]>("openai");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError(null);
    try {
      const model = (await sendJson<ChatModel>("POST", "/api/v1/model-connections", {
        provider,
        model: data.get("model"),
        ...(provider === "ollama" ? {} : { api_key: data.get("api_key") }),
      }))!;
      form.reset();
      await qc.invalidateQueries({ queryKey: ["chat-models"] });
      onChange(model.id);
      setOpen(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not save model connection.");
    } finally {
      const key = form.elements.namedItem("api_key");
      if (key instanceof HTMLInputElement) key.value = "";
      setBusy(false);
    }
  }

  async function remove(id: string) {
    setBusy(true);
    setError(null);
    try {
      await sendJson("DELETE", `/api/v1/model-connections/${id}`);
      await qc.invalidateQueries({ queryKey: ["chat-models"] });
      if (value === id) onChange(catalog?.default_id === id ? "" : (catalog?.default_id ?? ""));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not remove model connection.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="shrink-0 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
      <div className="mx-auto max-w-3xl">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <label className="flex min-w-0 flex-1 items-center gap-2 text-sm font-medium">
            <span>Model</span>
            <select
              aria-label="Chat model"
              value={value}
              onChange={(event) => onChange(event.target.value)}
              disabled={disabled || busy || loading || !catalog?.items.length}
              className={`${INPUT} min-w-0 max-w-sm truncate font-medium`}
            >
              {!value && <option value="">{loading ? "Loading models…" : "Choose a model"}</option>}
              {catalog?.items.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.model} · {model.provider}
                  {model.source === "personal" ? " · personal" : ""}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="ghost"
            disabled={disabled || busy}
            aria-expanded={open}
            aria-controls="chat-model-connections"
            onClick={() => {
              setOpen(!open);
              setError(null);
            }}
          >
            {open ? "Close" : "Manage models"}
          </Button>
        </div>
        {failed && (
          <div className="mt-2">
            <Alert tone="error">Could not load models. Refresh the page to retry.</Alert>
          </div>
        )}
        {!loading && !failed && !value && (
          <p className="mt-2 text-sm text-zinc-500">Choose an available model or add a connection to start chatting.</p>
        )}
        {open && (
          <section
            id="chat-model-connections"
            aria-label="Model connections"
            className="mt-3 max-h-[55vh] space-y-4 overflow-y-auto rounded-xl border border-zinc-200 p-4 dark:border-zinc-700"
          >
            <div>
              <h2 className="font-semibold">Add a model</h2>
              <p className="mt-1 text-sm text-zinc-500">
                Your added models are private to you in this workspace. API keys are encrypted on the server and never
                shown again.
              </p>
            </div>
            <form onSubmit={(event) => void add(event)} className="space-y-3">
              <label className="block space-y-1 text-sm font-medium">
                <span>Provider</span>
                <select
                  aria-label="Model provider"
                  value={provider}
                  onChange={(event) => setProvider(event.target.value as ChatModel["provider"])}
                  disabled={busy}
                  className={INPUT}
                >
                  <option value="openai">OpenAI</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="ollama">Ollama</option>
                </select>
              </label>
              <Field
                label="Model ID"
                name="model"
                required
                maxLength={160}
                disabled={busy}
                autoComplete="off"
                placeholder="Exact model ID from your provider"
              />
              {provider === "ollama" ? (
                <p className="text-sm text-zinc-500">
                  Use a model already installed on the server&apos;s configured Ollama instance. No API key is needed.
                </p>
              ) : (
                <Field
                  key={provider}
                  label="Provider API key"
                  name="api_key"
                  type="password"
                  required
                  minLength={8}
                  maxLength={4096}
                  disabled={busy}
                  autoComplete="new-password"
                />
              )}
              <p className="text-xs text-zinc-500">
                Messages and relevant document excerpts are sent to the selected provider. Cloud providers may charge
                your account.
              </p>
              <Button type="submit" disabled={busy}>
                {busy ? "Saving…" : "Save and select model"}
              </Button>
            </form>
            {error && <Alert tone="error">{error}</Alert>}
            {catalog?.items.some((model) => model.source === "personal") && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold">Your connections</h3>
                {catalog.items
                  .filter((model) => model.source === "personal")
                  .map((model) => (
                    <div key={model.id} className="flex items-center justify-between gap-2 text-sm">
                      <span className="min-w-0 break-all">
                        {model.model} · {model.provider}
                      </span>
                      <Button
                        variant="ghost"
                        disabled={busy}
                        aria-label={`Remove ${model.model} connection`}
                        onClick={() => void remove(model.id)}
                      >
                        Remove
                      </Button>
                    </div>
                  ))}
              </div>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
