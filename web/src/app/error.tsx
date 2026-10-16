"use client";

import { ThemeToggle } from "@/components/theme";

// Generic message only: never render error details or stacks to the user (OWASP A05/A09).
export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="relative grid min-h-dvh place-items-center p-6 text-center">
      <div className="absolute top-3 right-3">
        <ThemeToggle />
      </div>
      <div>
        <h1 className="text-xl font-semibold">Something went wrong</h1>
        <p className="mt-2 text-sm text-zinc-500">Please try again.</p>
        <button
          onClick={reset}
          className="mt-4 rounded-lg bg-zinc-900 px-4 py-2 text-sm text-white dark:bg-zinc-100 dark:text-zinc-900"
        >
          Retry
        </button>
      </div>
    </main>
  );
}
