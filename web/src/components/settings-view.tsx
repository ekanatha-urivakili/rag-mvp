"use client";

import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useCan, useMe } from "@/components/app-shell";
import { ThemeSettings } from "@/components/theme";
import { ProfileSettings } from "@/components/profile-settings";
import { Alert, Button, Field, Pagination } from "@/components/ui";
import { ApiError, getJson, sendJson } from "@/lib/client";
import type { ApiKey, AuditEvent, CreatedApiKey, Role } from "@/lib/types";

const SIZE = 25;
const ROLES: Role[] = ["viewer", "editor", "admin"];

export function SettingsView() {
  const keys = useCan("apikey:manage");
  const audit = useCan("audit:read");
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-8 px-4 py-6">
        <h1 className="text-xl font-semibold">Settings</h1>
        <ThemeSettings />
        <ProfileSettings />
        {keys && <ApiKeys />}
        {audit && <AuditLog />}
      </div>
    </div>
  );
}

function ApiKeys() {
  const me = useMe();
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<CreatedApiKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const keys = useQuery({
    queryKey: ["api-keys", me.tenant.id, page],
    queryFn: ({ signal }) => getJson<ApiKey[]>(`/api/v1/api-keys?limit=${SIZE}&offset=${page * SIZE}`, signal),
  });

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError(null);
    setCreated(null);
    setCopied(false);
    try {
      setCreated(
        await sendJson<CreatedApiKey>("POST", "/api/v1/api-keys", { name: data.get("name"), role: data.get("role") }),
      );
      form.reset();
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["api-keys"] }),
        qc.invalidateQueries({ queryKey: ["audit"] }),
      ]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not create API key");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(key: ApiKey) {
    setBusy(true);
    setError(null);
    try {
      await sendJson("DELETE", `/api/v1/api-keys/${key.id}`);
      if (created?.id === key.id) setCreated(null);
      setRevoking(null);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["api-keys"] }),
        qc.invalidateQueries({ queryKey: ["audit"] }),
      ]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not revoke API key");
    } finally {
      setBusy(false);
    }
  }

  async function copy() {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.api_key);
      setCopied(true);
    } catch {
      setError("Could not copy automatically. Select and copy the key below.");
    }
  }

  return (
    <section aria-labelledby="api-keys-heading" className="space-y-4">
      <h2 id="api-keys-heading" className="text-lg font-semibold">
        API keys{" "}
        <span className="group relative inline-flex align-middle">
          <button
            type="button"
            aria-label="What are API keys?"
            aria-describedby="api-key-help"
            className="rounded-full border border-zinc-400 px-1.5 text-xs"
          >
            !
          </button>
          <span
            id="api-key-help"
            role="tooltip"
            className="absolute left-0 top-full z-20 mt-2 hidden w-64 rounded-lg bg-zinc-900 p-3 text-sm font-normal text-white shadow-lg group-hover:block group-focus-within:block"
          >
            API keys let scripts and integrations access this workspace without your password. You do not need a key to
            use this app. Keep keys secret and revoke unused keys.
          </span>
        </span>
      </h2>
      <p className="text-sm text-zinc-500">
        Optional: create a key only when connecting a script or external integration. Choose the least access it needs.
      </p>
      <form
        onSubmit={(event) => void create(event)}
        className="flex flex-wrap items-end gap-3 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800"
      >
        <div className="min-w-48 flex-1">
          <Field label="Key name" name="name" maxLength={100} required disabled={busy} />
        </div>
        <label className="space-y-1 text-sm">
          <span className="block font-medium">Key role</span>
          <select
            name="role"
            defaultValue="viewer"
            disabled={busy}
            className="rounded-lg border border-zinc-300 bg-transparent px-3 py-2 dark:border-zinc-700"
          >
            {ROLES.map((role) => (
              <option key={role}>{role}</option>
            ))}
          </select>
        </label>
        <Button type="submit" disabled={busy}>
          Create key
        </Button>
      </form>
      {error && <Alert tone="error">{error}</Alert>}
      {created && (
        <div className="space-y-3 rounded-xl border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950">
          <p className="text-sm font-medium">Copy {created.name} now. This secret is shown only once.</p>
          <Field label="New API key" value={created.api_key} readOnly autoComplete="off" spellCheck={false} />
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => void copy()}>
              {copied ? "Copied" : "Copy key"}
            </Button>
            <Button variant="ghost" onClick={() => setCreated(null)}>
              Dismiss secret
            </Button>
          </div>
        </div>
      )}
      {keys.isPending && <p>Loading keys…</p>}
      {keys.isError && <Alert tone="error">Could not load API keys.</Alert>}
      {keys.isSuccess && (
        <>
          {keys.data.length === 0 ? (
            <Alert tone="info">No API keys on this page.</Alert>
          ) : (
            <ul className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
              {keys.data.map((key) => (
                <li key={key.id} className="flex flex-wrap items-center justify-between gap-3 p-3 text-sm">
                  <div>
                    <p className="font-medium">
                      {key.name} · {key.role}
                      {key.revoked_at && " · revoked"}
                    </p>
                    <p className="font-mono text-xs text-zinc-500">rk_{key.key_prefix}_…</p>
                    <p className="text-xs text-zinc-500">
                      Last used: {key.last_used_at ? new Date(key.last_used_at).toLocaleString() : "Never"}
                    </p>
                  </div>
                  {!key.revoked_at &&
                    (revoking === key.id ? (
                      <div className="flex items-center gap-2">
                        <span>Revoke access?</span>
                        <Button variant="danger" disabled={busy} onClick={() => void revoke(key)}>
                          Revoke
                        </Button>
                        <Button variant="ghost" disabled={busy} onClick={() => setRevoking(null)}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <Button variant="ghost" disabled={busy} onClick={() => setRevoking(key.id)}>
                        Revoke
                      </Button>
                    ))}
                </li>
              ))}
            </ul>
          )}
          <Pagination
            page={page}
            onChange={setPage}
            hasNext={keys.data.length === SIZE}
            busy={keys.isFetching || busy}
          />
        </>
      )}
    </section>
  );
}

function AuditLog() {
  const me = useMe();
  const audit = useInfiniteQuery({
    queryKey: ["audit", me.tenant.id],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) =>
      getJson<AuditEvent[]>(
        `/api/v1/audit-log?limit=${SIZE}${pageParam === null ? "" : `&before_id=${pageParam}`}`,
        signal,
      ),
    getNextPageParam: (last) => (last.length === SIZE ? last.at(-1)?.id : undefined),
  });
  const events = audit.data?.pages.flat() ?? [];
  return (
    <section aria-labelledby="audit-heading" className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h2 id="audit-heading" className="text-lg font-semibold">
          Audit history
        </h2>
        <Button variant="secondary" disabled={audit.isFetching} onClick={() => void audit.refetch()}>
          Refresh history
        </Button>
      </div>
      <p className="text-sm text-zinc-500">Security and workspace changes, newest first.</p>
      {audit.isPending && <p>Loading audit history…</p>}
      {audit.isError && <Alert tone="error">Could not load audit history.</Alert>}
      {audit.isSuccess && events.length === 0 && <Alert tone="info">No audit events yet.</Alert>}
      <ul className="space-y-2">
        {events.map((event) => (
          <li key={event.id} className="rounded-xl border border-zinc-200 p-3 text-sm dark:border-zinc-800">
            <div className="flex flex-wrap justify-between gap-2">
              <p className="font-medium">{event.action}</p>
              <time dateTime={event.created_at} className="text-zinc-500">
                {new Date(event.created_at).toLocaleString()}
              </time>
            </div>
            <p className="break-all text-xs text-zinc-500">
              Actor: {event.actor_user_id || "System"}
              {event.target_id ? ` · ${event.target_type || "Target"}: ${event.target_id}` : ""}
              {event.ip ? ` · IP: ${event.ip}` : ""}
            </p>
            {Object.keys(event.metadata).length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer">Event details</summary>
                <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all text-xs">
                  {JSON.stringify(event.metadata, null, 2)}
                </pre>
              </details>
            )}
          </li>
        ))}
      </ul>
      {audit.hasNextPage && (
        <Button variant="secondary" disabled={audit.isFetching} onClick={() => void audit.fetchNextPage()}>
          {audit.isFetchingNextPage ? "Loading…" : "Load older events"}
        </Button>
      )}
    </section>
  );
}
