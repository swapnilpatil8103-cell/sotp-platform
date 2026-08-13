"use client";

import { useEffect, useState } from "react";
import { getMemo } from "@/lib/api";
import type { MemoResponse } from "@/lib/types";
import { AIBadge, Card, ErrorState, PageSkeleton, SectionHeading } from "@/components/ui";

/**
 * Investment Memo viewer. GET /memo/{ticker} now assembles real persisted
 * data (Company, FinancialFacts, Segments, latest ValuationRun per method)
 * and drafts each section via backend/ai/tasks/memo_generator.py, with
 * every number traced back to that data. If no completed valuation run
 * exists yet for the ticker, the backend 404s with a clear "insufficient
 * data" message rather than fabricating a memo, and this page renders that
 * as an honest error state.
 */
export default function MemoPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [memo, setMemo] = useState<MemoResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getMemo(ticker)
      .then((m) => !cancelled && setMemo(m))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Investment Memo"
        description={`AI-assisted research memo for ${ticker}, sourced from an approved valuation run.`}
      />
      <div className="flex items-center gap-2">
        <AIBadge label="AI-drafted, human-reviewed" />
      </div>

      {loading && <PageSkeleton />}
      {!loading && Boolean(error) && (
        <ErrorState
          error={error}
          context="GET /memo/{ticker} — no completed valuation run exists yet for this company, or the backend is unavailable"
        />
      )}
      {!loading && !error && memo && (
        <div className="space-y-4">
          <Card>
            <p className="text-sm text-[#111827]/70">
              {memo.company_name} ({memo.ticker}) &middot; company_id {memo.company_id}
            </p>
          </Card>
          {memo.sections.map((section) => (
            <Card key={section.section} title={section.section}>
              {section.abstained ? (
                <p className="text-sm italic text-[#B45309]">
                  {section.reason ?? "AI abstained: insufficient traceable data for this section."}
                </p>
              ) : (
                <p className="whitespace-pre-wrap text-sm text-[#111827]/80">{section.memo_text}</p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
