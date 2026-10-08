"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useCan } from "@/components/app-shell";
import { FileUpload } from "@/components/file-upload";
import { Alert, Button } from "@/components/ui";
import { ApiError, getJson, sendJson } from "@/lib/client";
import { formatBytes } from "@/lib/format";
import type { DocumentItem, Page } from "@/lib/types";

const STEP: Record<string, string> = {
  reading: "Reading file",
  ocr: "Running OCR",
  extracting: "Extracting fields",
  indexing: "Indexing",
};

function progressText(d: DocumentItem): string {
  const p = d.progress;
  if (!p?.step) return d.status;
  const step = STEP[p.step] ?? p.step;
  return p.model ? `${step} with ${p.model} (${p.provider})` : step;
}

export function DocumentsView() {
  const canWrite = useCan("document:write");
  const canDelete = useCan("document:delete");
  const docs = useQuery({
    queryKey: ["documents"],
    queryFn: ({ signal }) => getJson<Page<DocumentItem>>("/api/v1/documents?limit=100", signal),
    // Poll only while something is still being ingested.
    refetchInterval: (q) =>
      q.state.data?.items.some((d) => d.status === "queued" || d.status === "processing") ? 3000 : false,
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">Documents</h1>
        </div>
        {canWrite && <FileUpload kind="document" />}

        {docs.isPending && <p className="text-sm text-zinc-500">Loading…</p>}
        {docs.isError && <Alert tone="error">Could not load documents.</Alert>}
        {docs.isSuccess && docs.data.items.length === 0 && <Alert tone="info">No documents yet.</Alert>}
        {docs.isSuccess && docs.data.items.length > 0 && (
          <div className="overflow-x-auto rounded-xl border border-zinc-200 dark:border-zinc-800">
            <table className="w-full text-left text-sm">
              <thead className="bg-zinc-50 text-xs text-zinc-500 dark:bg-zinc-900">
                <tr>
                  <th className="px-3 py-2 font-medium">Title</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                  <th className="px-3 py-2 font-medium">Version</th>
                  <th className="px-3 py-2 font-medium">Chunks</th>
                  <th className="px-3 py-2 font-medium">Size</th>
                  <th className="px-3 py-2 font-medium">Updated</th>
                  {canDelete && <th className="px-3 py-2" />}
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                {docs.data.items.map((d) => (
                  <DocumentRow key={d.id} d={d} canDelete={canDelete} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

const BADGE: Partial<Record<DocumentItem["status"], string>> = {
  ready: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
};

function DocumentRow({ d, canDelete }: { d: DocumentItem; canDelete: boolean }) {
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const badge = BADGE[d.status] ?? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300";

  async function remove() {
    setDeleting(true);
    try {
      await sendJson("DELETE", `/api/v1/documents/${d.id}`);
      await qc.invalidateQueries({ queryKey: ["documents"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Delete failed");
      setConfirming(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <tr>
      <td className="max-w-xs px-3 py-2">
        <p className="truncate font-medium" title={d.title}>
          {d.title}
        </p>
        {d.error && <p className="text-xs text-red-600">{d.error}</p>}
        {error && <p className="text-xs text-red-600">{error}</p>}
      </td>
      <td className="px-3 py-2">
        <span className={`rounded-full px-2 py-0.5 text-xs ${badge}`}>
          {d.status === "ready" || d.status === "failed" ? d.status : progressText(d)}
        </span>
      </td>
      <td className="px-3 py-2">{d.version}</td>
      <td className="px-3 py-2">{d.chunk_count}</td>
      <td className="px-3 py-2 whitespace-nowrap">{formatBytes(d.size_bytes)}</td>
      <td className="px-3 py-2 whitespace-nowrap">{new Date(d.updated_at).toLocaleString()}</td>
      {canDelete && (
        <td className="px-3 py-2 text-right whitespace-nowrap">
          {confirming ? (
            <span className="inline-flex gap-1">
              <Button variant="danger" className="px-2 py-1 text-xs" disabled={deleting} onClick={() => void remove()}>
                {deleting ? "Deleting…" : "Delete"}
              </Button>
              <Button
                variant="ghost"
                className="px-2 py-1 text-xs"
                disabled={deleting}
                onClick={() => setConfirming(false)}
              >
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              variant="ghost"
              className="px-2 py-1 text-xs"
              aria-label={`Delete ${d.title}`}
              onClick={() => setConfirming(true)}
            >
              Delete
            </Button>
          )}
        </td>
      )}
    </tr>
  );
}
