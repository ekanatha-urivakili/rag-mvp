"use client";

import { useEffect, useSyncExternalStore } from "react";

type Theme = "system" | "light" | "dark";
const KEY = "moontig-theme";
function snapshot(): Theme {
  const value = localStorage.getItem(KEY);
  return value === "light" || value === "dark" ? value : "system";
}
function subscribe(callback: () => void) {
  window.addEventListener("storage", callback);
  window.addEventListener("theme-change", callback);
  return () => {
    window.removeEventListener("storage", callback);
    window.removeEventListener("theme-change", callback);
  };
}
function useTheme() {
  return useSyncExternalStore(subscribe, snapshot, (): Theme => "system");
}
function setTheme(theme: Theme) {
  localStorage.setItem(KEY, theme);
  window.dispatchEvent(new Event("theme-change"));
}
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useTheme();
  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const dark = theme === "dark" || (theme === "system" && media.matches);
      document.documentElement.dataset.theme = dark ? "dark" : "light";
    };
    apply();
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  return children;
}
export function ThemeSettings() {
  const theme = useTheme();
  return (
    <section className="space-y-3" aria-labelledby="appearance-heading">
      <h2 id="appearance-heading" className="text-lg font-semibold">
        Appearance
      </h2>
      <label className="block max-w-xs space-y-1 text-sm">
        <span className="block font-medium">Theme</span>
        <select
          value={theme}
          onChange={(event) => setTheme(event.target.value as Theme)}
          className="w-full rounded-lg border border-zinc-300 bg-transparent py-2 dark:border-zinc-700"
        >
          <option value="system">Use device setting</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </label>
      <p className="text-sm text-zinc-500">Saved on this browser.</p>
    </section>
  );
}

const OPTIONS: { value: Theme; label: string; icon: string }[] = [
  { value: "system", label: "Use device setting", icon: "M3 5h18v11H3z M8 20h8 M12 16v4" },
  {
    value: "light",
    label: "Light",
    icon: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4",
  },
  { value: "dark", label: "Dark", icon: "M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z" },
];
export function ThemeToggle() {
  const theme = useTheme();
  return (
    <div
      role="radiogroup"
      aria-label="Theme"
      className="inline-flex shrink-0 rounded-lg border border-zinc-200 p-0.5 dark:border-zinc-800"
    >
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={theme === o.value}
          aria-label={o.label}
          title={o.label}
          onClick={() => setTheme(o.value)}
          className={`rounded-md p-1.5 ${theme === o.value ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d={o.icon} />
          </svg>
        </button>
      ))}
    </div>
  );
}
