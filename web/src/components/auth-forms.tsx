"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ApiError, sendJson } from "@/lib/client";
import { Alert, Button, Field } from "@/components/ui";

type Status = { tone: "error" | "success"; text: string } | null;

function useSubmit() {
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>(null);
  async function run(action: () => Promise<string | void>) {
    setBusy(true);
    setStatus(null);
    try {
      const done = await action();
      if (done) setStatus({ tone: "success", text: done });
    } catch (e) {
      setStatus({ tone: "error", text: e instanceof ApiError ? e.message : "Something went wrong." });
    } finally {
      setBusy(false);
    }
  }
  return { busy, status, setStatus, run };
}

function fields(e: FormEvent<HTMLFormElement>): Record<string, string> {
  e.preventDefault();
  return Object.fromEntries([...new FormData(e.currentTarget)].map(([k, v]) => [k, String(v)]));
}

/** After sign-in, re-render server components with the new session cookie. */
function useEnterApp() {
  const router = useRouter();
  return () => {
    router.replace("/");
    router.refresh();
  };
}

/** Email links carry ?token=…; drop it from the address bar and history as soon as the page loads. */
function useStripTokenFromUrl() {
  useEffect(() => {
    window.history.replaceState(null, "", window.location.pathname);
  }, []);
}

export function LoginForms() {
  const enterApp = useEnterApp();
  const [tab, setTab] = useState<"signin" | "signup" | "forgot">("signin");
  const { busy, status, setStatus, run } = useSubmit();
  const tabs = [
    ["signin", "Sign in"],
    ["signup", "Sign up"],
    ["forgot", "Reset password"],
  ] as const;

  return (
    <div>
      <div
        role="tablist"
        className="mb-5 grid grid-cols-3 rounded-lg bg-zinc-100 p-1 text-xs font-medium dark:bg-zinc-800"
      >
        {tabs.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => {
              setTab(id);
              setStatus(null);
            }}
            className={`rounded-md py-1.5 ${tab === id ? "bg-white shadow-sm dark:bg-zinc-950" : "text-zinc-500"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "signin" && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            const f = fields(e);
            void run(async () => {
              await sendJson("POST", "/api/auth/login", { email: f.email, password: f.password });
              enterApp();
            });
          }}
        >
          <Field label="Email" name="email" type="email" autoComplete="username" required maxLength={320} />
          <Field
            label="Password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            maxLength={128}
          />
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      )}

      {tab === "signup" && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            const f = fields(e);
            void run(async () => {
              await sendJson("POST", "/api/auth/signup", { email: f.email });
              return "If you can register with this email, a verification link is on its way.";
            });
          }}
        >
          <p className="text-sm text-zinc-500">Create a private workspace. We&apos;ll verify your email first.</p>
          <Field label="Email" name="email" type="email" autoComplete="email" required maxLength={320} />
          <Button type="submit" disabled={busy} className="w-full">
            Send verification link
          </Button>
        </form>
      )}

      {tab === "forgot" && (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            const f = fields(e);
            void run(async () => {
              await sendJson("POST", "/api/auth/forgot", { email: f.email });
              return "If an account exists for that email, a reset link is on its way.";
            });
          }}
        >
          <Field label="Account email" name="email" type="email" autoComplete="email" required maxLength={320} />
          <Button type="submit" disabled={busy} className="w-full">
            Send reset link
          </Button>
        </form>
      )}

      {status && (
        <div className="mt-4">
          <Alert tone={status.tone}>{status.text}</Alert>
        </div>
      )}
    </div>
  );
}

function MissingToken({ what }: { what: string }) {
  return (
    <div className="space-y-4">
      <Alert tone="info">Open this page from the link in your {what} email.</Alert>
      <Link href="/login" className="block text-center text-sm text-indigo-600 underline dark:text-indigo-400">
        Back to sign in
      </Link>
    </div>
  );
}

export function VerifySignupForm({ token }: { token: string | null }) {
  useStripTokenFromUrl();
  const enterApp = useEnterApp();
  const { busy, status, setStatus, run } = useSubmit();
  if (!token) return <MissingToken what="verification" />;
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        const f = fields(e);
        if (f.password !== f.password2) return setStatus({ tone: "error", text: "Passwords don't match." });
        void run(async () => {
          await sendJson("POST", "/api/auth/signup-verify", {
            token,
            password: f.password,
            workspace_name: f.workspace,
          });
          enterApp();
        });
      }}
    >
      <h1 className="text-lg font-semibold">Create your account</h1>
      <Field label="Workspace name" name="workspace" required maxLength={160} />
      <Field
        label="Password (12–128 characters)"
        name="password"
        type="password"
        autoComplete="new-password"
        required
        minLength={12}
        maxLength={128}
      />
      <Field
        label="Repeat password"
        name="password2"
        type="password"
        autoComplete="new-password"
        required
        maxLength={128}
      />
      <Button type="submit" disabled={busy} className="w-full">
        Create account
      </Button>
      {status && <Alert tone={status.tone}>{status.text}</Alert>}
    </form>
  );
}

export function ResetPasswordForm({ token }: { token: string | null }) {
  useStripTokenFromUrl();
  const { busy, status, setStatus, run } = useSubmit();
  if (!token) return <MissingToken what="password reset" />;
  if (status?.tone === "success") {
    return (
      <div className="space-y-4">
        <Alert tone="success">{status.text}</Alert>
        <Link href="/login" className="block text-center text-sm text-indigo-600 underline dark:text-indigo-400">
          Sign in
        </Link>
      </div>
    );
  }
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        const f = fields(e);
        if (f.password !== f.password2) return setStatus({ tone: "error", text: "Passwords don't match." });
        void run(async () => {
          await sendJson("POST", "/api/auth/reset", { token, new_password: f.password });
          return "Password updated. Sign in with your new password.";
        });
      }}
    >
      <h1 className="text-lg font-semibold">Choose a new password</h1>
      <Field
        label="New password (12–128 characters)"
        name="password"
        type="password"
        autoComplete="new-password"
        required
        minLength={12}
        maxLength={128}
      />
      <Field
        label="Repeat password"
        name="password2"
        type="password"
        autoComplete="new-password"
        required
        maxLength={128}
      />
      <Button type="submit" disabled={busy} className="w-full">
        Set password
      </Button>
      {status && <Alert tone={status.tone}>{status.text}</Alert>}
    </form>
  );
}

export function AcceptInviteForm({ token }: { token: string | null }) {
  useStripTokenFromUrl();
  const enterApp = useEnterApp();
  const { busy, status, run } = useSubmit();
  if (!token) return <MissingToken what="invitation" />;
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        const f = fields(e);
        void run(async () => {
          await sendJson("POST", "/api/auth/accept-invite", { token, password: f.password || null });
          enterApp();
        });
      }}
    >
      <h1 className="text-lg font-semibold">Join your team</h1>
      <p className="text-sm text-zinc-500">
        New here? Choose a password (12–128 characters). Already have an account? Enter your current password.
      </p>
      <Field
        label="Password"
        name="password"
        type="password"
        autoComplete="current-password"
        required
        maxLength={128}
      />
      <p className="text-xs text-zinc-500">
        <Link href="/login" className="text-indigo-600 underline dark:text-indigo-400">
          Forgot your password?
        </Link>{" "}
        Reset it from the sign-in page, then open this link again.
      </p>
      <Button type="submit" disabled={busy} className="w-full">
        Accept invitation
      </Button>
      {status && <Alert tone={status.tone}>{status.text}</Alert>}
    </form>
  );
}
