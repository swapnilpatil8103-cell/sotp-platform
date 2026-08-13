"use client";

import { useState } from "react";
import { computeSotp, ApiError } from "@/lib/api";
import type { SotpInput, SotpResult, SotpSegmentInput } from "@/lib/types";
import { Card, ErrorState, MetricCard, SectionHeading } from "@/components/ui";
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

  function updateSegment(i: number, patch: Partial<SotpSegmentInput>) {
    setSegments((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));
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

      <Card title="Segments">
        <div className="space-y-3">
          {segments.map((s, i) => (
            <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-4 items-center">
              <input
                className="rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm"
                value={s.name}
                onChange={(e) => updateSegment(i, { name: e.target.value })}
                placeholder="Segment name"
              />
              <input
                type="number"
                className="rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
                value={s.enterprise_value}
                onChange={(e) => updateSegment(i, { enterprise_value: Number(e.target.value) })}
                placeholder="Enterprise value"
              />
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
