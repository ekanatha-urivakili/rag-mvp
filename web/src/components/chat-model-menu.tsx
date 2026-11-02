"use client";

import { useLayoutEffect, useRef, useState } from "react";
import type { ChatModels, ReasoningEffort } from "@/lib/types";

export const EFFORT_LABELS: Record<ReasoningEffort, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

function modelName(model: string): string {
  return model.replace(
    /^gpt-([\d.]+)-([a-z]+)$/,
    (_: string, version: string, name: string) => `GPT-${version} ${name.charAt(0).toUpperCase()}${name.slice(1)}`,
  );
}

export type ModelPickerProps = {
  catalog: ChatModels | undefined;
  value: string;
  onChange: (id: string) => void;
  effort: ReasoningEffort | null;
  onEffortChange: (effort: ReasoningEffort) => void;
  disabled: boolean;
  loading: boolean;
  failed: boolean;
};

export function ChatModelMenu({
  catalog,
  value,
  onChange,
  effort,
  onEffortChange,
  disabled,
  loading,
  failed,
  onManage,
}: ModelPickerProps & { onManage: () => void }) {
  const trigger = useRef<HTMLButtonElement>(null);
  const popup = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<"effort" | "models">("effort");
  const [open, setOpen] = useState(false);
  const selected = catalog?.items.find((model) => model.id === value);
  const choices = selected?.reasoning_efforts ?? [];
  const level = Math.max(0, choices.indexOf(effort ?? "medium"));
  const currentEffort = effort ?? choices[level] ?? "medium";
  const name = selected ? modelName(selected.model) : loading ? "Loading…" : "Select model";

  useLayoutEffect(() => {
    function position() {
      if (!open || !trigger.current || !popup.current) return;
      const anchor = trigger.current.getBoundingClientRect();
      const { width, height } = popup.current.getBoundingClientRect();
      const below = anchor.bottom + 8;
      popup.current.style.left = `${Math.max(16, Math.min(anchor.right - width, window.innerWidth - width - 16))}px`;
      popup.current.style.top = `${Math.max(16, below + height <= window.innerHeight - 16 ? below : anchor.top - height - 8)}px`;
    }
    position();
    if (open) {
      popup.current
        ?.querySelector<HTMLElement>(
          view === "models" ? '[aria-checked="true"], [role="menuitemradio"]' : 'input[type="range"]',
        )
        ?.focus();
    }
    window.addEventListener("resize", position);
    return () => window.removeEventListener("resize", position);
  }, [open, view, catalog]);

  function hide() {
    popup.current?.hidePopover();
  }

  return (
    <div className="shrink-0">
      <button
        ref={trigger}
        type="button"
        popoverTarget="chat-model-popover"
        aria-label={`Model and reasoning effort: ${name}${effort ? `, ${EFFORT_LABELS[effort]}` : ""}`}
        aria-expanded={open}
        disabled={disabled || loading}
        onClick={() => setView(choices.length ? "effort" : "models")}
        className="model-pill inline-flex max-w-[220px] items-center gap-1.5 rounded-full bg-zinc-100 px-3 py-2 text-sm text-zinc-800 transition-colors hover:bg-zinc-200 focus-visible:outline-2 focus-visible:outline-blue-500 disabled:opacity-50 sm:max-w-[280px] dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700"
        title={selected?.model}
      >
        <span className="truncate">{name.replace(/^GPT-/, "")}</span>
        {effort && <span className="text-zinc-500 dark:text-zinc-400">{EFFORT_LABELS[effort]}</span>}
        <svg
          className="shrink-0 text-zinc-400"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          aria-hidden="true"
        >
          <path d="m7 10 5 5 5-5" />
        </svg>
      </button>
      <div
        ref={popup}
        id="chat-model-popover"
        popover="auto"
        role="dialog"
        aria-label="Model and reasoning settings"
        onToggle={(event) => setOpen(event.newState === "open")}
        className="model-popover fixed m-0 w-[336px] max-w-[calc(100vw-32px)] rounded-[22px] border border-zinc-200 bg-white p-2 text-zinc-900 shadow-[0_8px_32px_rgba(0,0,0,0.12)] dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      >
        {view === "effort" && choices.length > 0 ? (
          <div className="px-1 pb-1">
            <div className="grid grid-cols-[32px_1fr_32px] items-start px-1 pt-1">
              <button
                type="button"
                aria-label="Use low effort"
                onClick={() => onEffortChange(choices[0]!)}
                className="rounded-full p-1 text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden="true"
                >
                  <path d="m13 2-9 12h7l-1 8 10-13h-7z" />
                </svg>
              </button>
              <div className="text-center">
                <p className="text-lg font-medium text-blue-500" aria-live="polite">
                  {EFFORT_LABELS[currentEffort]}
                </p>
                <button
                  type="button"
                  onClick={() => setView("models")}
                  className="mx-auto flex items-center justify-center gap-1 rounded px-2 py-0.5 text-xs text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
                  aria-label="Select model"
                >
                  {name.replace(/^GPT-/, "")} <span aria-hidden="true">›</span>
                </button>
              </div>
              <button
                type="button"
                aria-label="Reset effort to medium"
                onClick={() => onEffortChange(choices.includes("medium") ? "medium" : choices[0]!)}
                className="rounded-full p-1 text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  aria-hidden="true"
                >
                  <path d="M4 9a8 8 0 1 1 1 9M4 3v6h6" />
                </svg>
              </button>
            </div>
            <div className="relative mt-4 h-8">
              <input
                type="range"
                min={0}
                max={choices.length - 1}
                step={1}
                value={level}
                onChange={(event) => onEffortChange(choices[Number(event.target.value)]!)}
                aria-label="Reasoning effort"
                aria-valuetext={EFFORT_LABELS[currentEffort]}
                className="effort-slider absolute inset-0 z-10 h-8 w-full cursor-pointer rounded-full"
                style={{
                  background: `linear-gradient(to right, #3b82f6 ${(level / (choices.length - 1)) * 100}%, var(--effort-track) ${(level / (choices.length - 1)) * 100}%)`,
                }}
              />
              <div
                className="pointer-events-none absolute inset-0 z-20 flex items-center justify-between px-3.5"
                aria-hidden="true"
              >
                {choices.map((choice) => (
                  <span key={choice} className="size-1 rounded-full bg-zinc-400/60" />
                ))}
              </div>
            </div>
            <p className="mt-2 px-1 text-center text-[11px] text-zinc-500">
              Higher effort may take longer and use more tokens.
            </p>
          </div>
        ) : (
          <>
            <div className="flex items-center justify-between px-2 pt-1 pb-2">
              <h2 className="text-sm text-zinc-500">Select model</h2>
              {choices.length > 0 && (
                <button
                  type="button"
                  onClick={() => setView("effort")}
                  className="rounded px-2 text-xs text-zinc-500 hover:text-blue-500"
                >
                  Effort settings
                </button>
              )}
            </div>
            {failed && (
              <p role="alert" className="px-2 py-3 text-sm text-red-600">
                Could not load models. Refresh to retry.
              </p>
            )}
            <div
              className="max-h-[min(320px,45vh)] overflow-y-auto"
              role="menu"
              aria-label="Select model"
              onKeyDown={(event) => {
                if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
                const options = Array.from(
                  event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=menuitemradio]"),
                );
                if (!options.length) return;
                event.preventDefault();
                const current = options.indexOf(document.activeElement as HTMLButtonElement);
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? options.length - 1
                      : (current + (event.key === "ArrowUp" ? -1 : 1) + options.length) % options.length;
                options[next]!.focus();
              }}
            >
              {catalog?.items.map((model) => (
                <button
                  key={model.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={value === model.id}
                  onClick={() => {
                    onChange(model.id);
                    hide();
                  }}
                  className="flex w-full items-center justify-between gap-3 rounded-2xl px-3 py-2.5 text-left hover:bg-zinc-100 focus-visible:bg-zinc-100 focus-visible:outline-none dark:hover:bg-zinc-800 dark:focus-visible:bg-zinc-800"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm">{modelName(model.model)}</span>
                    <span className="block text-xs text-zinc-400">
                      {model.provider}
                      {model.source === "personal" ? " · Personal connection" : " · Workspace model"}
                    </span>
                  </span>
                  {value === model.id && (
                    <svg
                      className="shrink-0"
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      aria-hidden="true"
                    >
                      <path d="m5 12 4 4 10-10" />
                    </svg>
                  )}
                </button>
              ))}
              {!catalog?.items.length && !failed && (
                <p className="px-3 py-4 text-sm text-zinc-500">Add a connection to start chatting.</p>
              )}
            </div>
            <div className="mt-2 border-t border-zinc-100 pt-1 dark:border-zinc-800">
              <button
                type="button"
                onClick={() => {
                  hide();
                  onManage();
                }}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800"
              >
                <span className="text-lg" aria-hidden="true">
                  +
                </span>{" "}
                Manage models &amp; keys
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
