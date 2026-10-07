"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useState } from "react";
import { ApiError, sendJson } from "@/lib/client";
import { navigationFor } from "@/lib/navigation";
import { Brand, Footer } from "@/components/brand";
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
        <div className="flex h-dvh flex-col">
          <TopBar />
          <div className="min-h-0 flex-1">{children}</div>
          <Footer />
          <MobileNavigation />
        </div>
      </QueryClientProvider>
    </MeContext.Provider>
  );
}

function TopBar() {
  const me = useMe();
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const others = me.tenants.filter((t) => t.id !== me.tenant.id);
  const nav = navigationFor(me.permissions, pathname);

  async function switchTo(tenantId: string) {
    try {
      await sendJson("POST", "/api/auth/switch-tenant", { tenant_id: tenantId });
      qc.clear(); // conversations and documents are per workspace
      router.replace("/");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not switch workspace");
    }
  }

  async function signOut() {
    await sendJson("POST", "/api/auth/logout").catch(() => undefined);
    qc.clear();
    router.replace("/login");
    router.refresh();
  }

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
      <Brand />
      <nav aria-label="Workspace" className="hidden gap-1 text-sm md:flex">
        {nav.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            aria-current={n.active ? "page" : undefined}
            className={`shrink-0 rounded-md px-2.5 py-1.5 ${n.active ? "bg-zinc-100 font-medium dark:bg-zinc-800" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100"}`}
          >
            {n.label}
          </Link>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-3 text-sm">
        {error && <span className="text-red-600">{error}</span>}
        {others.length > 0 ? (
          <select
            aria-label="Switch workspace"
            value={me.tenant.id}
            onChange={(e) => void switchTo(e.target.value)}
            className="max-w-48 truncate rounded-md border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700"
          >
            {[me.tenant, ...others].map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · {t.role}
              </option>
            ))}
          </select>
        ) : (
          <span className="hidden max-w-48 truncate text-zinc-500 xl:inline" title={me.tenant.name}>
            {me.tenant.name} · {me.tenant.role}
          </span>
        )}
        <span className="hidden max-w-56 truncate text-zinc-500 xl:inline" title={me.email ?? undefined}>
          {me.email}
        </span>
        <button
          onClick={() => void signOut()}
          className="rounded-md px-2 py-1 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}

const ICON_PATHS: Record<string, string> = {
  "/": "m3 10 9-7 9 7v11h-6v-7H9v7H3z",
  "/documents": "M6 3h9l4 4v14H6z M15 3v5h4 M12 17V11m-3 3 3-3 3 3",
  "/receipts": "M8 6 10 3h4l2 3h5v15H3V6z M16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  "/members": "M9 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6 M3 21v-3a6 6 0 0 1 12 0v3 M17 4a3 3 0 0 1 0 6 M18 13a5 5 0 0 1 3 5v3",
  "/settings": "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
};
function MobileNavigation() {
  const me = useMe();
  const pathname = usePathname();
  return (
    <nav
      aria-label="Mobile workspace"
      className="flex shrink-0 justify-around border-t border-zinc-200 bg-white pb-[env(safe-area-inset-bottom)] md:hidden dark:border-zinc-800 dark:bg-zinc-950"
    >
      {navigationFor(me.permissions, pathname).map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={item.active ? "page" : undefined}
          className={`flex min-w-0 flex-1 flex-col items-center gap-1 px-1 py-3 text-[10px] ${item.active ? "font-semibold text-teal-700 dark:text-teal-400" : "text-zinc-500"}`}
        >
          <svg
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d={ICON_PATHS[item.href]} />
          </svg>
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
