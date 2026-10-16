"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useCan, useMe } from "@/components/app-shell";
import { Alert, Button } from "@/components/ui";
import { getJson } from "@/lib/client";
import type { DocumentItem, Page } from "@/lib/types";

export function UsageView() {
  const canRead = useCan("document:read");
  if (!canRead)
    return (
      <div className="p-6">
        <Alert tone="error">You do not have permission to view workspace usage.</Alert>
      </div>
    );
  return <UsageSummary />;
}

function UsageSummary() {
  const me = useMe();
  const usage = useQuery({
    queryKey: ["usage", me.tenant.id],
    queryFn: async ({ signal }) => {
      const [documents, receipts] = await Promise.all([
        getJson<Page<DocumentItem>>("/api/v1/documents?limit=1", signal),
        getJson<Page<DocumentItem>>("/api/v1/documents?kind=receipt&limit=1", signal),
      ]);
      return { documents: documents.total, receipts: receipts.total };
    },
  });
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl space-y-8 px-4 py-8 sm:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Workspace usage</h1>
            <p className="mt-2 text-sm text-zinc-500">A snapshot of the files in {me.tenant.name}.</p>
          </div>
          <Button variant="secondary" disabled={usage.isFetching} onClick={() => void usage.refetch()}>
            {usage.isFetching ? "Refreshing…" : "Refresh usage"}
          </Button>
        </div>
        {usage.isPending && (
          <p role="status" className="text-sm text-zinc-500">
            Loading workspace usage…
          </p>
        )}
        {usage.isError && <Alert tone="error">Could not load usage. Refresh to try again.</Alert>}
        {usage.isSuccess && (
          <>
            <dl className="grid gap-4 sm:grid-cols-3">
              {[
                { label: "Total uploads", value: usage.data.documents + usage.data.receipts },
                { label: "Documents", value: usage.data.documents },
                { label: "Receipt uploads", value: usage.data.receipts },
              ].map((item) => (
                <div key={item.label} className="rounded-2xl border border-zinc-200 p-6 dark:border-zinc-800">
                  <dt className="text-sm text-zinc-500">{item.label}</dt>
                  <dd className="mt-3 text-4xl font-semibold tracking-tight">{item.value.toLocaleString()}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-zinc-500">
              Includes queued, processing, ready, and failed uploads across this workspace. Deleted files are excluded.
            </p>
          </>
        )}
        <section className="rounded-2xl bg-zinc-50 p-6 dark:bg-zinc-900" aria-labelledby="usage-details">
          <h2 id="usage-details" className="font-semibold">
            About these numbers
          </h2>
          <p className="mt-2 text-sm leading-6 text-zinc-500">
            Counts reflect current uploads, rather than monthly activity. Token usage, storage totals, billing, and plan
            limits are not currently available.
          </p>
          <div className="mt-4 flex flex-wrap gap-4 text-sm">
            <Link href="/documents" className="underline underline-offset-4">
              Manage documents
            </Link>
            <Link href="/receipts" className="underline underline-offset-4">
              Manage receipts
            </Link>
          </div>
        </section>
      </div>
    </div>
  );
}
