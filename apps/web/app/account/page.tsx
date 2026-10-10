"use client";

import Link from "next/link";
import { useState } from "react";

export default function AccountPage() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setMessage(null);
    if (next !== confirm) {
      setMessage({ kind: "error", text: "The new passwords do not match." });
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (res.status === 401) {
        window.location.assign("/login");
        return;
      }
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const wait = res.headers.get("retry-after");
        setMessage({
          kind: "error",
          text: (typeof body?.detail === "string" ? body.detail : "Could not change the password.") +
            (res.status === 429 && wait ? ` Try again in ${wait}s.` : ""),
        });
        return;
      }
      setCurrent("");
      setNext("");
      setConfirm("");
      setMessage({ kind: "ok", text: "Password changed. Every other session has been signed out." });
    } catch {
      setMessage({ kind: "error", text: "Could not reach the server." });
    } finally {
      setBusy(false);
    }
  }

  const field =
    "w-full rounded-[10px] border border-[#E5E7EB] px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#2563EB]";
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6">
      <h1 className="mb-1 text-2xl font-semibold tracking-tight text-[#0B1F3A]">Change password</h1>
      <p className="mb-8 text-sm text-[#111827]/60">At least 12 characters, different from the current one.</p>
      <form onSubmit={onSubmit} className="w-full max-w-sm space-y-4">
        <div>
          <label htmlFor="current" className="mb-1 block text-xs font-medium text-[#111827]/60">
            Current password
          </label>
          <input id="current" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} className={field} />
        </div>
        <div>
          <label htmlFor="new" className="mb-1 block text-xs font-medium text-[#111827]/60">
            New password
          </label>
          <input id="new" type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} className={field} />
        </div>
        <div>
          <label htmlFor="confirm" className="mb-1 block text-xs font-medium text-[#111827]/60">
            Repeat new password
          </label>
          <input id="confirm" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} className={field} />
        </div>
        {message && (
          <p role="alert" className={`text-sm ${message.kind === "ok" ? "text-[#16A34A]" : "text-[#DC2626]"}`}>
            {message.text}
          </p>
        )}
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-[10px] bg-[#0B1F3A] px-4 py-2.5 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Change password"}
        </button>
        <Link href="/" className="block text-center text-xs text-[#111827]/60 underline">
          Back
        </Link>
      </form>
    </main>
  );
}
