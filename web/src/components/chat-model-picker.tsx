"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ChatModelMenu, type ModelPickerProps } from "@/components/chat-model-menu";
import { Alert, Button, Field } from "@/components/ui";
import { ApiError, sendJson } from "@/lib/client";
import type { ChatModel } from "@/lib/types";

const INPUT =
  "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-700 dark:bg-zinc-950";

export function ChatModelPicker({
  catalog,
  value,
  onChange,
  disabled,
  loading,
  failed,
  effort,
  onEffortChange,
}: ModelPickerProps) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<ChatModel["provider"]>("openai");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);

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
    <>
      <ChatModelMenu
        catalog={catalog}
        value={value}
        onChange={onChange}
        effort={effort}
        onEffortChange={onEffortChange}
        disabled={disabled || busy}
        loading={loading}
        failed={failed}
        onManage={() => {
          setError(null);
          setOpen(true);
        }}
      />
      <dialog
        ref={dialog}
        aria-label="Model connections"
        onClose={() => {
          setOpen(false);
          dialog.current?.querySelector("form")?.reset();
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget && !busy) setOpen(false);
        }}
        className="m-auto max-h-[85dvh] w-[calc(100%-32px)] max-w-lg overflow-y-auto rounded-3xl border border-zinc-200 bg-white p-6 text-zinc-900 shadow-xl backdrop:bg-black/25 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      >
        <section className="space-y-4">
          <div>
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-semibold">Add a model</h2>
              <Button
                type="button"
                variant="ghost"
                disabled={busy}
                onClick={() => setOpen(false)}
                aria-label="Close model connections"
              >
                ✕
              </Button>
            </div>
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
              Messages and relevant document excerpts are sent to the selected provider. Cloud providers may charge your
              account.
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
      </dialog>
    </>
  );
}
