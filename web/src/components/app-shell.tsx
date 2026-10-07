"use client";

import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useState } from "react";
import { ApiError, sendJson } from "@/lib/client";
import { navigationFor } from "@/lib/navigation";
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
      <span className="shrink-0 text-sm font-semibold">RAG Assistant</span>
      <nav
        aria-label="Workspace"
        className="order-last flex w-full gap-1 overflow-x-auto text-sm lg:order-none lg:w-auto"
      >
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
