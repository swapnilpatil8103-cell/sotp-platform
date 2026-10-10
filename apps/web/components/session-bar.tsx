"use client";

import { useEffect, useState } from "react";

/** Shows who is signed in and offers sign-out. Reads /api/auth/me (the token itself is never exposed). */
export function SessionBar() {
  const [subject, setSubject] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/me", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setSubject(d?.subject ?? null))
      .catch(() => setSubject(null));
  }, []);

  async function signOut() {
    await fetch("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    window.location.assign("/login");
  }

  if (!subject) return null;
  return (
    <div className="flex items-center gap-3 text-xs text-[#111827]/60">
      <span>{subject}</span>
      <button onClick={signOut} className="rounded-[6px] border border-[#E5E7EB] px-2 py-1 hover:border-[#2563EB] hover:text-[#2563EB]">
        Sign out
      </button>
    </div>
  );
}
