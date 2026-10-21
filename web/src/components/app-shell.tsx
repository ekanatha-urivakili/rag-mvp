"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useRef, useState } from "react";
import { ApiError, sendJson } from "@/lib/client";
import { navigationFor } from "@/lib/navigation";
import { Brand } from "@/components/brand";
import { ThemeToggle } from "@/components/theme";
import { HistorySidebar } from "@/components/history-sidebar";
import type { Me } from "@/lib/types";

const MeContext = createContext<Me | null>(null);

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe must be used inside <AppShell>");
  return me;
}

/** UX only: hides actions the role can't perform. The API enforces every permission. */
export function useCan(permission: string): boolean {
  return useMe().permissions.includes(permission);
}

export function AppShell({ me, children }: { me: Me; children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 15_000,
            retry: (count, e) => !(e instanceof ApiError && e.status < 500) && count < 2,
          },
        },
      }),
  );
  return (
    <MeContext.Provider value={me}>
      <QueryClientProvider client={queryClient}>
        <WorkspaceLayout>{children}</WorkspaceLayout>
      </QueryClientProvider>
    </MeContext.Provider>
  );
}

function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const drawer = useRef<HTMLDialogElement>(null);
  return (
    <div className="flex h-dvh overflow-hidden">
      <a
        href="#workspace-content"
        className="sr-only z-50 rounded-lg bg-white p-3 text-zinc-900 focus:not-sr-only focus:absolute"
      >
        Skip to content
      </a>
      <div className="hidden w-64 shrink-0 md:block">
        <WorkspaceSidebar />
      </div>
      <dialog
        ref={drawer}
        aria-label="Workspace navigation"
        className="m-0 h-dvh max-h-dvh w-80 max-w-[90vw] bg-zinc-50 p-0 text-zinc-900 backdrop:bg-black/40 md:hidden dark:bg-zinc-900 dark:text-zinc-100"
        onClick={(event) => {
          if (event.target === event.currentTarget) drawer.current?.close();
        }}
      >
        <WorkspaceSidebar onNavigate={() => drawer.current?.close()} />
      </dialog>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 px-4 sm:px-6">
          <button
            type="button"
            onClick={() => drawer.current?.showModal()}
            className="rounded-lg p-2 hover:bg-zinc-100 md:hidden dark:hover:bg-zinc-800"
            aria-label="Open navigation"
          >
            <NavIcon href="menu" />
          </button>
          <div className="ml-auto">
            <ThemeToggle />
          </div>
        </header>
        <main id="workspace-content" tabIndex={-1} className="min-h-0 flex-1 outline-none">
          {children}
        </main>
        <footer className="flex shrink-0 flex-wrap items-center justify-center gap-x-4 gap-y-1 px-4 py-2 text-[11px] text-zinc-500">
          <span>Moontig can make mistakes. Check answers against their sources.</span>
          <Link href="/settings#help" className="underline underline-offset-2">
            Help &amp; settings
          </Link>
        </footer>
      </div>
    </div>
  );
}

function WorkspaceSidebar({ onNavigate }: { onNavigate?: () => void }) {
  const me = useMe();
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const nav = navigationFor(me.permissions, pathname);
  const activeId = pathname.startsWith("/c/") ? (pathname.split("/")[2] ?? null) : null;
  const account = me.name || me.email || "My account";

  async function switchTo(tenantId: string) {
    setSwitching(true);
    setError(null);
    try {
      await sendJson("POST", "/api/auth/switch-tenant", { tenant_id: tenantId });
      qc.clear();
      onNavigate?.();
      router.replace("/");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not switch workspace");
    } finally {
      setSwitching(false);
    }
  }

  async function signOut() {
    try {
      await sendJson("POST", "/api/auth/logout");
      qc.clear();
      router.replace("/login");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not sign out. Try again.");
    }
  }

  return (
    <aside className="flex h-full flex-col overflow-y-auto bg-zinc-50 dark:bg-zinc-900">
      <div className="flex h-16 shrink-0 items-center justify-between px-4">
        <Brand />
        {onNavigate && (
          <button
            onClick={onNavigate}
            aria-label="Close navigation"
            className="rounded-lg p-2 hover:bg-zinc-200 dark:hover:bg-zinc-800"
          >
            Close
          </button>
        )}
      </div>
      <nav aria-label="Workspace" className="shrink-0 space-y-1 px-3 pb-3">
        {nav
          .filter((item) => item.href !== "/settings" && item.href !== "/usage")
          .map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={onNavigate}
              aria-current={item.active ? "page" : undefined}
              className={`flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm ${item.active ? "bg-zinc-200/70 font-medium dark:bg-zinc-800" : "hover:bg-zinc-200/50 dark:hover:bg-zinc-800/70"}`}
            >
              <NavIcon href={item.href} />
              {item.href === "/" ? "New chat" : item.label}
            </Link>
          ))}
      </nav>
      {me.permissions.includes("chat:use") ? (
        <HistorySidebar activeId={activeId} onNavigate={onNavigate} />
      ) : (
        <div className="flex-1" />
      )}
      <div className="shrink-0 space-y-2 border-t border-zinc-200 p-3 dark:border-zinc-800">
        <nav aria-label="Account" className="space-y-1">
          {nav
            .filter((item) => item.href === "/settings" || item.href === "/usage")
            .map((item) => (
              <Link
                key={item.href}
                href={item.href}
                onClick={onNavigate}
                aria-current={item.active ? "page" : undefined}
                className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm hover:bg-zinc-200/50 dark:hover:bg-zinc-800 ${item.active ? "bg-zinc-200/70 font-medium dark:bg-zinc-800" : ""}`}
              >
                <NavIcon href={item.href} />
                {item.label}
              </Link>
            ))}
        </nav>
        {error && (
          <p role="alert" className="px-2 text-xs text-red-600 dark:text-red-400">
            {error}
          </p>
        )}
        <details className="group rounded-xl bg-white dark:bg-zinc-950">
          <summary className="flex cursor-pointer list-none items-center gap-3 rounded-xl p-3 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-teal-700 text-xs font-semibold text-white">
              {account.slice(0, 2).toUpperCase()}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{account}</span>
              <span className="block text-xs capitalize text-zinc-500">{me.tenant.role} workspace</span>
            </span>
            <span aria-hidden="true">⌃</span>
          </summary>
          <div className="space-y-3 px-3 pb-3">
            <label className="block text-xs text-zinc-500">
              Workspace
              <select
                aria-label="Switch workspace"
                value={me.tenant.id}
                disabled={switching}
                onChange={(event) => void switchTo(event.target.value)}
                className="mt-1 w-full rounded-lg border border-zinc-200 bg-transparent py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:text-zinc-100"
              >
                {[me.tenant, ...me.tenants.filter((tenant) => tenant.id !== me.tenant.id)].map((tenant) => (
                  <option key={tenant.id} value={tenant.id}>
                    {tenant.name} · {tenant.role}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => void signOut()}
              className="w-full rounded-lg border border-zinc-200 py-2 text-sm hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Sign out
            </button>
          </div>
        </details>
      </div>
    </aside>
  );
}

const ICON_PATHS: Record<string, string> = {
  "/": "M12 5v14M5 12h14",
  "/documents": "M6 3h9l4 4v14H6z M15 3v5h4 M9 12h7m-7 4h7",
  "/receipts": "M6 3h12v18l-3-2-3 2-3-2-3 2z M9 8h6m-6 4h6",
  "/members": "M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6 M3 21v-3a6 6 0 0 1 12 0v3 M17 4a3 3 0 0 1 0 6 M18 13a5 5 0 0 1 3 5v3",
  "/settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
  "/usage": "M4 20h16M7 16v-5m5 5V4m5 12V8",
  menu: "M4 6h16M4 12h16M4 18h16",
};
function NavIcon({ href }: { href: string }) {
  return (
    <svg
      width="19"
      height="19"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={ICON_PATHS[href]} />
    </svg>
  );
}
