"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { useMe } from "@/components/app-shell";
import { Alert, Button, Field } from "@/components/ui";
import { ApiError, sendJson } from "@/lib/client";

export function ProfileSettings() {
  const me = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "error" | "success"; text: string } | null>(null);
  if (!me.user_id) return null;
  async function save(event: FormEvent<HTMLFormElement>, password: boolean) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setNote(null);
    if (password && data.get("new_password") !== data.get("confirm_password")) {
      setNote({ tone: "error", text: "New passwords do not match." });
      return;
    }
    setBusy(true);
    try {
      await sendJson(
        password ? "POST" : "PATCH",
        password ? "/api/v1/auth/password/change" : "/api/v1/me",
        password
          ? {
              current_password: data.get("current_password"),
              new_password: data.get("new_password"),
            }
          : { name: data.get("name") },
      );
      if (password) {
        form.reset();
        await sendJson("POST", "/api/auth/logout").catch(() => undefined);
        qc.clear();
        router.replace("/login");
        router.refresh();
      } else setNote({ tone: "success", text: "Profile saved." });
    } catch (error) {
      setNote({ tone: "error", text: error instanceof ApiError ? error.message : "Could not save changes." });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-labelledby="profile-heading" className="space-y-4">
      <h2 id="profile-heading" className="text-lg font-semibold">
        Profile
      </h2>
      <form
        onSubmit={(event) => void save(event, false)}
        className="max-w-lg space-y-3 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800"
      >
        <Field
          label="Name"
          name="name"
          autoComplete="name"
          defaultValue={me.name ?? ""}
          maxLength={100}
          required
          disabled={busy}
        />
        <Field label="Email" type="email" value={me.email ?? ""} readOnly autoComplete="email" />
        <p className="text-xs text-zinc-500">Your verified sign-in email.</p>
        <Button type="submit" disabled={busy}>
          Save profile
        </Button>
      </form>
      <form
        onSubmit={(event) => void save(event, true)}
        className="max-w-lg space-y-3 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800"
      >
        <h3 className="font-semibold">Change password</h3>
        <Field
          label="Current password"
          name="current_password"
          type="password"
          autoComplete="current-password"
          maxLength={128}
          required
          disabled={busy}
        />
        <Field
          label="New password"
          name="new_password"
          type="password"
          autoComplete="new-password"
          maxLength={128}
          required
          disabled={busy}
        />
        <Field
          label="Confirm new password"
          name="confirm_password"
          type="password"
          autoComplete="new-password"
          maxLength={128}
          required
          disabled={busy}
        />
        <p className="text-xs text-zinc-500">
          Use a long, unique passphrase. Changing your password signs you out on all devices.
        </p>
        <Button type="submit" disabled={busy}>
          Change password
        </Button>
      </form>
      {note && <Alert tone={note.tone}>{note.text}</Alert>}
    </section>
  );
}
