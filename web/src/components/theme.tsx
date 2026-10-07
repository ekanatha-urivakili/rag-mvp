"use client";

import { useEffect, useSyncExternalStore } from "react";

type Theme = "system" | "light" | "dark";
const KEY = "folionest-theme";
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
          onChange={(event) => {
            localStorage.setItem(KEY, event.target.value);
            window.dispatchEvent(new Event("theme-change"));
          }}
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
