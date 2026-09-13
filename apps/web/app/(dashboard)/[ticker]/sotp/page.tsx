"use client";

import { useState } from "react";
import { autoValueSegments, computeSotp, ApiError } from "@/lib/api";
import type {
  AutoValueSegmentsRequest,
  SegmentValuationSuggestion,
  SotpInput,
  SotpResult,
  SotpSegmentInput,
} from "@/lib/types";
import { AIBadge, Card, ErrorState, MetricCard, SectionHeading } from "@/components/ui";
import { SotpWaterfallChart } from "@/components/charts";

const DEFAULT_SEGMENTS: SotpSegmentInput[] = [
  { name: "Segment A", enterprise_value: 50_000_000_000, ownership_pct: 1.0 },
  { name: "Segment B", enterprise_value: 20_000_000_000, ownership_pct: 1.0 },
];

/**
 * Fully interactive SOTP bridge builder. Segment EVs are typically produced
 * by running DCF/comps per segment (see the DCF/Comps tabs) -- this page
 * lets an analyst assemble the final bridge from those pre-computed EVs
 * plus balance-sheet figures and submit directly to POST /valuation/sotp.
 */
export default function SotpPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [segments, setSegments] = useState<SotpSegmentInput[]>(DEFAULT_SEGMENTS);
  const [cash, setCash] = useState(10_000_000_000);
  const [debt, setDebt] = useState(15_000_000_000);
  const [minorityInterest, setMinorityInterest] = useState(0);
  const [overheadAnnual, setOverheadAnnual] = useState(1_000_000_000);
  const [overheadMultiple, setOverheadMultiple] = useState(8);
  const [treatment, setTreatment] = useState<SotpInput["corporate_overhead_treatment"]>("direct_deduction");
  const [shares, setShares] = useState(1_000_000_000);
  const [sharePrice, setSharePrice] = useState<number | "">("");

  const [result, setResult] = useState<SotpResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  // Auto-value (SUGGESTED, requires review) state: uniform EV/Revenue
  // multiple applied across all segments via POST /valuation/{ticker}/segments/auto-value.
  const [autoMultiple, setAutoMultiple] = useState(3.0);
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoError, setAutoError] = useState<unknown>(null);
  const [suggestedIdx, setSuggestedIdx] = useState<Set<number>>(new Set());

  function updateSegment(i: number, patch: Partial<SotpSegmentInput>) {
    setSegments((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
    // Editing a field after a suggestion was applied means it's now a
    // human-reviewed/edited value, not the raw AI suggestion anymore.
    setSuggestedIdx((prev) => {
      const next = new Set(prev);
      next.delete(i);
      return next;
    });
  }

  async function runAutoValue() {
    setAutoLoading(true);
    setAutoError(null);
    try {
      // Uses each segment's current "enterprise_value" field as a stand-in
      // REPORTED revenue figure for this quick uniform-multiple suggestion
      // flow -- in a full integration this would come from
      // GET /companies/{ticker}/segments' REPORTED revenue facts per
      // segment. The multiple itself is always an explicit human input
      // (autoMultiple), never invented.
      const payload: AutoValueSegmentsRequest = {
        segments: segments.map((s) => ({
          segment: {
            name: s.name,
            revenue: s.enterprise_value > 0 ? s.enterprise_value : null,
            revenue_status: s.enterprise_value > 0 ? "REPORTED" : "MISSING",
          },
          methodology: "multiple",
          multiple_assumption: { metric: "ev_to_revenue", multiple_value: autoMultiple },
        })),
      };
      const resp = await autoValueSegments(ticker, payload);
      const applied = new Set<number>();
      setSegments((prev) =>
        prev.map((s, i) => {
          const suggestion: SegmentValuationSuggestion | undefined = resp.suggestions[i];
          if (suggestion && suggestion.status === "SUGGESTED" && suggestion.suggested_enterprise_value != null) {
            applied.add(i);
            return { ...s, enterprise_value: suggestion.suggested_enterprise_value };
          }
          return s;
        })
      );
      setSuggestedIdx(applied);
    } catch (err) {
      setAutoError(err);
    } finally {
      setAutoLoading(false);
    }
  }

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    const payload: SotpInput = {
      segments,
      cash_and_equivalents: cash,
      marketable_securities: 0,
      other_investments: 0,
      total_debt: debt,
      minority_interest: minorityInterest,
      corporate_liabilities: 0,
      corporate_overhead_annual: overheadAnnual,
      overhead_capitalization_multiple: overheadMultiple,
      corporate_overhead_treatment: treatment,
      diluted_shares_outstanding: shares,
      current_share_price: sharePrice === "" ? null : Number(sharePrice),
    };
    try {
      const r = await computeSotp(payload);
      setResult(r);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="SOTP Bridge"
        description={`Assemble the sum-of-the-parts bridge for ${ticker} and compute it via POST /valuation/sotp. Segment EVs should come from per-segment DCF/comps runs.`}
      />

      <Card
        title="Segments"
        action={
          <div className="flex items-center gap-2">
            <label className="text-xs text-[#111827]/60">EV/Revenue multiple</label>
            <input
              type="number"
              step="0.1"
              className="w-20 rounded-[8px] border border-[#E5E7EB] px-2 py-1 text-xs tabular-nums"
              value={autoMultiple}
              onChange={(e) => setAutoMultiple(Number(e.target.value))}
            />
            <button
              onClick={runAutoValue}
              disabled={autoLoading}
              className="rounded-[8px] border border-[#2563EB]/40 bg-[#0B1F3A] px-3 py-1.5 text-xs font-medium text-white hover:bg-[#0B1F3A]/90 disabled:opacity-50"
            >
              {autoLoading ? "Suggesting…" : "Auto-suggest segment EVs"}
            </button>
          </div>
        }
      >
        <p className="mb-3 text-xs text-[#111827]/50">
          Auto-suggest calls <code>POST /valuation/{ticker}/segments/auto-value</code> to pre-fill each
          segment&apos;s enterprise value using the multiple above applied to its current EV field (as a
          stand-in revenue figure). Suggested values are model-generated — review before use — and every
          field below remains fully editable.
        </p>
        {Boolean(autoError) && <ErrorState error={autoError} context={`POST /valuation/${ticker}/segments/auto-value`} />}
        <div className="space-y-3">
          {segments.map((s, i) => (
            <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-4 items-center">
              <input
                className="rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm"
                value={s.name}
                onChange={(e) => updateSegment(i, { name: e.target.value })}
                placeholder="Segment name"
              />
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
                  value={s.enterprise_value}
                  onChange={(e) => updateSegment(i, { enterprise_value: Number(e.target.value) })}
                  placeholder="Enterprise value"
                />
                {suggestedIdx.has(i) && (
                  <span title="AI/model-suggested — review before use">
                    <AIBadge label="Suggested" />
                  </span>
                )}
              </div>
              <input
                type="number"
                step="0.01"
                min={0}
                max={1}
                className="rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
                value={s.ownership_pct}
                onChange={(e) => updateSegment(i, { ownership_pct: Number(e.target.value) })}
                placeholder="Ownership %"
              />
              <button
                onClick={() => setSegments((prev) => prev.filter((_, idx) => idx !== i))}
                className="rounded-[8px] border border-[#DC2626]/30 px-3 py-2 text-xs text-[#DC2626] hover:bg-[#DC2626]/5"
              >
                Remove
              </button>
            </div>
          ))}
          <button
            onClick={() =>
              setSegments((prev) => [...prev, { name: `Segment ${prev.length + 1}`, enterprise_value: 0, ownership_pct: 1.0 }])
            }
            className="rounded-[8px] border border-[#2563EB]/30 px-3 py-1.5 text-xs font-medium text-[#2563EB] hover:bg-[#2563EB]/5"
          >
            + Add segment
          </button>
        </div>
      </Card>

      <Card title="Balance Sheet & Corporate Overhead">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <LabeledNumber label="Cash & equivalents" value={cash} onChange={setCash} />
          <LabeledNumber label="Total debt" value={debt} onChange={setDebt} />
          <LabeledNumber label="Minority interest" value={minorityInterest} onChange={setMinorityInterest} />
          <LabeledNumber label="Annual corporate overhead" value={overheadAnnual} onChange={setOverheadAnnual} />
          <LabeledNumber label="Overhead capitalization multiple" value={overheadMultiple} onChange={setOverheadMultiple} />
          <LabeledNumber label="Diluted shares outstanding" value={shares} onChange={setShares} />
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">
              Corporate overhead treatment
            </label>
            <select
              value={treatment}
              onChange={(e) => setTreatment(e.target.value as SotpInput["corporate_overhead_treatment"])}
              className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm"
            >
              <option value="direct_deduction">Direct deduction</option>
              <option value="capitalized_overhead">Capitalized overhead</option>
            </select>
          </div>
          <LabeledNumber
            label="Current share price (optional)"
            value={sharePrice}
            onChange={setSharePrice}
            optional
          />
        </div>
      </Card>

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50"
      >
        {loading ? "Computing…" : "Compute SOTP"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /valuation/sotp" />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <MetricCard label="Sum of Segment EVs" value={fmt(result.sum_of_segment_evs)} />
            <MetricCard label="Equity Value" value={fmt(result.equity_value)} tone="success" />
            <MetricCard label="Implied Price/Share" value={`$${result.implied_price_per_share.toFixed(2)}`} />
            <MetricCard
              label="Conglomerate Discount"
              value={result.conglomerate_discount_pct != null ? `${result.conglomerate_discount_pct.toFixed(1)}%` : "N/A (no market price)"}
              tone={
                result.conglomerate_discount_pct != null
                  ? result.conglomerate_discount_pct < 0
                    ? "danger"
                    : "success"
                  : "default"
              }
            />
          </div>
          <Card title="SOTP Bridge Waterfall">
            <SotpWaterfallChart result={result} />
          </Card>
        </>
      )}
    </div>
  );
}

function LabeledNumber({
  label,
  value,
  onChange,
  optional,
}: {
  label: string;
  value: number | "";
  onChange: (v: any) => void;
  optional?: boolean;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-[#111827]/60">
        {label} {optional && <span className="text-[#111827]/30">(optional)</span>}
      </label>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))}
        className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
      />
    </div>
  );
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  return `$${n.toFixed(0)}`;
}
