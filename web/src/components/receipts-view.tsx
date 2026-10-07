"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useCan, useMe } from "@/components/app-shell";
import { FileUpload } from "@/components/file-upload";
import { Alert, Button, Pagination } from "@/components/ui";
import { ApiError, getJson, sendJson } from "@/lib/client";
import type { DocumentItem, Page, Receipt, ReceiptDetail } from "@/lib/types";

const SIZE = 25;

function money(value: string | null, currency: string | null): string {
  if (value === null) return "—";
  return `${value}${currency ? ` ${currency}` : ""}`;
}

export function ReceiptsView() {
  const allowed = useCan("document:read");
  const canUpload = useCan("document:write");
  const me = useMe();
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const receipts = useQuery({
    queryKey: ["receipts", me.tenant.id, page],
    queryFn: ({ signal }) => getJson<Page<Receipt>>(`/api/v1/receipts?limit=${SIZE}&offset=${page * SIZE}`, signal),
    enabled: allowed,
    refetchInterval: () =>
      qc
        .getQueryData<Page<DocumentItem>>(["receipt-uploads"])
        ?.items.some((item) => ["queued", "processing"].includes(item.status))
        ? 3000
        : false,
  });
  const detail = useQuery({
    queryKey: ["receipt", me.tenant.id, selected],
    queryFn: ({ signal }) => getJson<ReceiptDetail>(`/api/v1/receipts/${selected}`, signal),
    enabled: allowed && selected !== null,
  });

  if (!allowed)
    return (
      <div className="p-6">
        <Alert tone="info">You don&apos;t have permission to read receipts.</Alert>
      </div>
    );
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold">Receipts</h1>
        </div>
        <p className="text-sm text-zinc-500">
          Scan a receipt photo or upload a PDF here to extract merchant, items and totals.
        </p>
        {canUpload && <FileUpload kind="receipt" />}
        <ReceiptUploads />
        <Button variant="secondary" disabled={receipts.isFetching} onClick={() => void receipts.refetch()}>
          Refresh receipts
        </Button>
        {receipts.isPending && <p>Loading receipts…</p>}
        {receipts.isError && <Alert tone="error">Could not load receipts.</Alert>}
        {receipts.isSuccess && (
          <>
            {receipts.data.items.length === 0 ? (
              <Alert tone="info">No receipts on this page yet.</Alert>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-zinc-200 dark:border-zinc-800">
                <table className="w-full text-left text-sm">
                  <thead className="bg-zinc-50 dark:bg-zinc-900">
                    <tr>
                      <th className="p-3">Merchant</th>
                      <th className="p-3">Date</th>
                      <th className="p-3">Total</th>
                      <th className="p-3">Items</th>
                      <th className="p-3">Review</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                    {receipts.data.items.map((receipt) => (
                      <tr key={receipt.document_id}>
                        <td className="p-3">
                          <button
                            aria-expanded={selected === receipt.document_id}
                            onClick={() => setSelected(receipt.document_id)}
                            className="text-left font-medium text-indigo-600 underline dark:text-indigo-400"
                          >
                            {receipt.merchant_name || receipt.title}
                          </button>
                          <p className="text-xs text-zinc-500">{receipt.title}</p>
                        </td>
                        <td className="p-3 whitespace-nowrap">{receipt.purchased_on || "—"}</td>
                        <td className="p-3 whitespace-nowrap">{money(receipt.total, receipt.currency)}</td>
                        <td className="p-3">{receipt.item_count ?? "—"}</td>
                        <td className="p-3">
                          {receipt.warnings.length ? `${receipt.warnings.length} issues` : "No issues"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Pagination
              page={page}
              onChange={(next) => {
                setPage(next);
                setSelected(null);
              }}
              hasNext={(page + 1) * SIZE < receipts.data.total}
              busy={receipts.isFetching}
            />
          </>
        )}
        {selected && (
          <section
            aria-label="Receipt details"
            className="space-y-4 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800"
          >
            <div className="flex justify-between">
              <h2 className="text-lg font-semibold">Receipt details</h2>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Close details
              </Button>
            </div>
            {detail.isPending && <p>Loading details…</p>}
            {detail.isError && (
              <Alert tone="error">Could not load this receipt. It may have been deleted or replaced.</Alert>
            )}
            {detail.data && <ReceiptCard receipt={detail.data} />}
          </section>
        )}
      </div>
    </div>
  );
}

function ReceiptCard({ receipt: r }: { receipt: ReceiptDetail }) {
  return (
    <>
      <h3 className="font-semibold">{r.merchant_name || "Unknown merchant"}</h3>
      {r.merchant_address && <p className="text-sm whitespace-pre-wrap">{r.merchant_address}</p>}
      {r.merchant_phone && <p className="text-sm">{r.merchant_phone}</p>}
      <div className="grid gap-4 text-sm sm:grid-cols-3">
        <div>
          <p className="text-zinc-500">Total</p>
          <p className="text-lg font-semibold">{money(r.total, r.currency)}</p>
        </div>
        <div>
          <p className="text-zinc-500">Purchased</p>
          <p>
            {r.purchased_on || "Unknown"} {r.purchased_time}
          </p>
        </div>
        <div>
          <p className="text-zinc-500">Payment</p>
          <p>
            {r.payment_method?.replaceAll("_", " ") || "Unknown"} {r.card_brand}
            {r.card_last4 ? ` · ending ${r.card_last4}` : ""}
          </p>
        </div>
      </div>
      {r.items.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="py-2">Item</th>
                <th className="p-2">Quantity</th>
                <th className="p-2">Unit price</th>
                <th className="p-2">Amount</th>
              </tr>
            </thead>
            <tbody>
              {r.items.map((item, i) => (
                <tr key={i} className="border-t border-zinc-200 dark:border-zinc-800">
                  <td className="py-2">{item.description}</td>
                  <td className="p-2">{item.quantity ?? "—"}</td>
                  <td className="p-2">{money(item.unit_price, r.currency)}</td>
                  <td className="p-2">{money(item.amount, r.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {r.discounts.map((discount, i) => (
        <p key={i} className="text-sm">
          Discount: {discount.description} · {money(discount.amount, r.currency)}
        </p>
      ))}
      <dl className="grid max-w-sm grid-cols-2 gap-2 text-sm">
        {(["subtotal", "discount_total", "tax", "tip", "total"] as const)
          .filter((key) => r[key] !== null)
          .map((key) => (
            <div key={key} className="contents">
              <dt className="capitalize">{key.replaceAll("_", " ")}</dt>
              <dd className="text-right">{money(r[key], r.currency)}</dd>
            </div>
          ))}
      </dl>
      {r.warnings.map((warning, i) => (
        <Alert key={i} tone="info">
          {warning}
        </Alert>
      ))}
      <p className="text-xs text-zinc-500">
        Extracted values may contain errors. Check the original receipt before relying on them.
      </p>
    </>
  );
}

function ReceiptUploads() {
  const qc = useQueryClient();
  const uploads = useQuery({
    queryKey: ["receipt-uploads"],
    queryFn: ({ signal }) => getJson<Page<DocumentItem>>("/api/v1/documents?kind=receipt&limit=100", signal),
    refetchInterval: (query) =>
      query.state.data?.items.some((item) => ["queued", "processing"].includes(item.status)) ? 3000 : false,
  });
  useEffect(() => {
    if (uploads.data) void qc.invalidateQueries({ queryKey: ["receipts"] });
  }, [uploads.data, qc]);
  return (
    <section aria-label="Receipt upload status" className="space-y-2">
      {uploads.isError && <Alert tone="error">Could not load receipt upload status.</Alert>}
      {uploads.data?.items.map((item) => (
        <ReceiptUploadRow key={item.id} item={item} />
      ))}
    </section>
  );
}

function ReceiptUploadRow({ item }: { item: DocumentItem }) {
  const canDelete = useCan("document:delete");
  const qc = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await sendJson("DELETE", `/api/v1/documents/${item.id}`);
      await Promise.all(
        ["receipt-uploads", "receipts", "receipt"].map((key) => qc.invalidateQueries({ queryKey: [key] })),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not delete receipt.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 rounded-lg border border-zinc-200 p-3 text-sm dark:border-zinc-800">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p>
          <span className="font-medium">{item.title}</span> · {item.progress?.step ?? item.status}
        </p>
        {canDelete &&
          (confirming ? (
            <div className="flex items-center gap-2">
              <span>Delete receipt?</span>
              <Button variant="danger" disabled={busy} onClick={() => void remove()}>
                Delete
              </Button>
              <Button variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="ghost" onClick={() => setConfirming(true)}>
              Delete receipt
            </Button>
          ))}
      </div>
      {item.error && <Alert tone="error">{item.error} Upload the receipt again to retry.</Alert>}
      {error && <Alert tone="error">{error}</Alert>}
      {item.status === "ready" && (
        <p className="text-xs text-zinc-500">
          Processing complete. If no receipt details appear below, structured extraction was unavailable or no receipt
          was detected.
        </p>
      )}
    </div>
  );
}
