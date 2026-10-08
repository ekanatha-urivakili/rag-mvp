"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useCan, useMe } from "@/components/app-shell";
import { Alert, Button, Field, Pagination } from "@/components/ui";
import { ApiError, getJson, sendJson } from "@/lib/client";
import type { Invitation, Member, Role } from "@/lib/types";

const SIZE = 25;
const ROLES: Role[] = ["viewer", "editor", "admin"];

export function MembersView() {
  const allowed = useCan("member:manage");
  const me = useMe();
  const qc = useQueryClient();
  const [page, setPage] = useState(0);
  const [invitePage, setInvitePage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [note, setNote] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  const members = useQuery({
    queryKey: ["members", me.tenant.id, page],
    queryFn: ({ signal }) => getJson<Member[]>(`/api/v1/members?limit=${SIZE}&offset=${page * SIZE}`, signal),
    enabled: allowed,
  });
  const invitations = useQuery({
    queryKey: ["invitations", me.tenant.id, invitePage],
    queryFn: ({ signal }) =>
      getJson<Invitation[]>(`/api/v1/invitations?limit=${SIZE}&offset=${invitePage * SIZE}`, signal),
    enabled: allowed,
  });

  async function change(method: string, path: string, body: unknown, message: string) {
    setBusy(true);
    setNote(null);
    try {
      await sendJson(method, path, body);
      setNote({ tone: "success", text: message });
      setRemoving(null);
      setRevoking(null);
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["members"] }),
        qc.invalidateQueries({ queryKey: ["invitations"] }),
        qc.invalidateQueries({ queryKey: ["audit"] }),
      ]);
      return true;
    } catch (error) {
      setNote({ tone: "error", text: error instanceof ApiError ? error.message : "Request failed" });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (
      await change(
        "POST",
        "/api/v1/invitations",
        { email: data.get("email"), role: data.get("role") },
        "Invitation sent. The recipient can join using their email link.",
      )
    ) {
      form.reset();
    }
  }

  if (!allowed)
    return (
      <div className="p-6">
        <Alert tone="info">You don&apos;t have permission to manage workspace members.</Alert>
      </div>
    );

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-6 px-4 py-6">
        <h1 className="text-xl font-semibold">Members</h1>
        <p className="text-sm text-zinc-500">Invite people and manage access to {me.tenant.name}.</p>
        <form
          onSubmit={(event) => void invite(event)}
          className="flex flex-wrap items-end gap-3 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800"
        >
          <div className="min-w-48 flex-1">
            <Field label="Email address" name="email" type="email" maxLength={320} required disabled={busy} />
          </div>
          <label className="space-y-1 text-sm">
            <span className="block font-medium">Role</span>
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
            Send invitation
          </Button>
        </form>
        <p className="text-sm text-zinc-500">
          Viewers can read and chat. Editors can also upload and delete documents. Admins manage members, API keys, and
          audit history.
        </p>
        {note && <Alert tone={note.tone}>{note.text}</Alert>}
        {members.isPending && <p>Loading members…</p>}
        {members.isError && <Alert tone="error">Could not load members.</Alert>}
        {members.isSuccess && (
          <>
            {members.data.length === 0 ? (
              <Alert tone="info">No members on this page.</Alert>
            ) : (
              <div className="overflow-x-auto rounded-xl border border-zinc-200 dark:border-zinc-800">
                <table className="w-full text-left text-sm">
                  <thead className="bg-zinc-50 dark:bg-zinc-900">
                    <tr>
                      <th className="p-3">Member</th>
                      <th className="p-3">Role</th>
                      <th className="p-3">Joined</th>
                      <th className="p-3">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                    {members.data.map((member) => (
                      <tr key={member.user_id}>
                        <td className="p-3">
                          {member.email}
                          {member.user_id === me.user_id && " (you)"}
                          {!member.is_active && " · inactive"}
                        </td>
                        <td className="p-3">
                          <select
                            aria-label={`Role for ${member.email}`}
                            value={member.role}
                            disabled={busy || member.user_id === me.user_id}
                            onChange={(event) =>
                              void change(
                                "PATCH",
                                `/api/v1/members/${member.user_id}`,
                                { role: event.target.value },
                                "Role updated.",
                              )
                            }
                            className="rounded-md border border-zinc-300 bg-transparent px-2 py-1 dark:border-zinc-700"
                          >
                            {ROLES.map((role) => (
                              <option key={role}>{role}</option>
                            ))}
                          </select>
                        </td>
                        <td className="p-3 whitespace-nowrap">{new Date(member.joined_at).toLocaleDateString()}</td>
                        <td className="p-3">
                          {member.user_id !== me.user_id &&
                            (removing === member.user_id ? (
                              <div className="flex items-center gap-2">
                                <span>Remove access?</span>
                                <Button
                                  variant="danger"
                                  disabled={busy}
                                  onClick={() =>
                                    void change(
                                      "DELETE",
                                      `/api/v1/members/${member.user_id}`,
                                      undefined,
                                      "Member removed.",
                                    )
                                  }
                                >
                                  Remove
                                </Button>
                                <Button variant="ghost" disabled={busy} onClick={() => setRemoving(null)}>
                                  Cancel
                                </Button>
                              </div>
                            ) : (
                              <Button variant="ghost" disabled={busy} onClick={() => setRemoving(member.user_id)}>
                                Remove
                              </Button>
                            ))}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <Pagination
              page={page}
              onChange={setPage}
              hasNext={members.data.length === SIZE}
              busy={members.isFetching || busy}
            />
          </>
        )}
        <h2 className="text-lg font-semibold">Pending invitations</h2>
        {invitations.isPending && <p>Loading invitations…</p>}
        {invitations.isError && <Alert tone="error">Could not load invitations.</Alert>}
        {invitations.isSuccess && (
          <>
            {invitations.data.length === 0 ? (
              <Alert tone="info">No pending invitations on this page.</Alert>
            ) : (
              <ul className="divide-y divide-zinc-200 rounded-xl border border-zinc-200 dark:divide-zinc-800 dark:border-zinc-800">
                {invitations.data.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 p-3 text-sm">
                    <span className="min-w-0 break-all">
                      {item.email} · {item.role}
                    </span>
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-zinc-500">Expires {new Date(item.expires_at).toLocaleString()}</span>
                      {revoking === item.id ? (
                        <>
                          <Button
                            variant="danger"
                            disabled={busy}
                            onClick={() =>
                              void change(
                                "DELETE",
                                `/api/v1/invitations/${item.id}`,
                                undefined,
                                `Invitation for ${item.email} revoked.`,
                              )
                            }
                          >
                            Revoke
                          </Button>
                          <Button variant="ghost" disabled={busy} onClick={() => setRevoking(null)}>
                            Cancel
                          </Button>
                        </>
                      ) : (
                        <Button
                          variant="ghost"
                          disabled={busy}
                          aria-label={`Revoke invitation for ${item.email}`}
                          onClick={() => setRevoking(item.id)}
                        >
                          Revoke
                        </Button>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <Pagination
              page={invitePage}
              onChange={setInvitePage}
              hasNext={invitations.data.length === SIZE}
              busy={invitations.isFetching || busy}
            />
          </>
        )}
      </div>
    </div>
  );
}
