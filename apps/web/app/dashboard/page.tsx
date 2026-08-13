"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

/**
 * Fallback for the old `/dashboard?ticker=X` route from Phase 1. The real
 * dashboard now lives at `/dashboard/[ticker]` (see
 * `app/(dashboard)/[ticker]/`). This page just redirects a bare ticker
 * query param, or asks for one if missing.
 */
export default function DashboardRedirectPage({
  searchParams,
}: {
  searchParams: { ticker?: string };
}) {
  const router = useRouter();
  const [ticker, setTicker] = useState(searchParams?.ticker ?? "");

  if (searchParams?.ticker) {
    if (typeof window !== "undefined") {
      router.replace(`/dashboard/${encodeURIComponent(searchParams.ticker.toUpperCase())}`);
    }
    return null;
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6 py-24 text-center">
      <p className="text-sm uppercase tracking-wide text-[#2563EB] font-medium">
        No ticker selected
      </p>
      <h1 className="mt-2 text-2xl font-semibold text-[#0B1F3A]">
        Enter a ticker to open its dashboard
      </h1>
      <div className="mt-6 flex items-center gap-3">
        <input
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && ticker.trim()) {
              router.push(`/dashboard/${encodeURIComponent(ticker.trim().toUpperCase())}`);
            }
          }}
          placeholder="e.g. AAPL"
          className="rounded-[10px] border border-[#0B1F3A]/20 px-4 py-2.5 text-[#0B1F3A] focus:outline-none focus:ring-2 focus:ring-[#2563EB]"
        />
        <button
          onClick={() =>
            ticker.trim() &&
            router.push(`/dashboard/${encodeURIComponent(ticker.trim().toUpperCase())}`)
          }
          className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90"
        >
          Go
        </button>
      </div>
    </main>
  );
}
