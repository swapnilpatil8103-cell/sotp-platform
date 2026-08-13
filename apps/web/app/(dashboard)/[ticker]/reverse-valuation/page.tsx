"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import type { DcfInput, ReverseValuationResult } from "@/lib/types";
import { Card, ClassificationBadge, ErrorState, MetricCard, SectionHeading } from "@/components/ui";

const BASE_DCF: DcfInput = {
  base_revenue: 100_000_000_000,
  revenue_growth_rates: [0.08, 0.08, 0.08, 0.08, 0.08],
  ebit_margins: [0.24, 0.24, 0.24, 0.24, 0.24],
  tax_rate: 0.21,
  da_pct_of_revenue: [0.04, 0.04, 0.04, 0.04, 0.04],
  capex_pct_of_revenue: [0.05, 0.05, 0.05, 0.05, 0.05],
  nwc_change_pct_of_revenue: [0.01, 0.01, 0.01, 0.01, 0.01],
  wacc: 0.09,
  terminal_growth_rate: 0.025,
  net_debt: 5_000_000_000,
  cash_and_equivalents: 20_000_000_000,
  investments: 0,
  minority_interest: 0,
  diluted_shares_outstanding: 1_000_000_000,
};

export default function ReverseValuationPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [targetEv, setTargetEv] = useState(600_000_000_000);
  const [solveTarget, setSolveTarget] = useState<"revenue_growth" | "terminal_growth">("revenue_growth");
  const [searchLow, setSearchLow] = useState(-0.1);
  const [searchHigh, setSearchHigh] = useState(0.5);
  const [histLow, setHistLow] = useState(0.02);
  const [histHigh, setHistHigh] = useState(0.12);
  const [peerLow, setPeerLow] = useState(0.03);
  const [peerHigh, setPeerHigh] = useState(0.15);

  const [result, setResult] = useState<ReverseValuationResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    const base = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";
    try {
      const res = await fetch(`${base}/scenarios/reverse-valuation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_dcf_input: BASE_DCF,
          target_enterprise_value: targetEv,
          solve_target: solveTarget,
          search_low: searchLow,
          search_high: searchHigh,
          historical_range: { low: histLow, high: histHigh },
          peer_range: { low: peerLow, high: peerHigh },
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Reverse valuation failed", body.detail);
      }
      setResult(await res.json());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Reverse Valuation"
        description={`What growth assumption does ${ticker}'s current market EV imply? Root-finds a single DCF assumption to reconcile a target EV, then classifies it against explicit historical/peer ranges. Submits to POST /scenarios/reverse-valuation.`}
      />

      <Card title="Inputs">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Num label="Target enterprise value" value={targetEv} onChange={setTargetEv} />
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">Solve for</label>
            <select value={solveTarget} onChange={(e) => setSolveTarget(e.target.value as any)} className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm">
              <option value="revenue_growth">Flat revenue growth rate</option>
              <option value="terminal_growth">Terminal growth rate</option>
            </select>
          </div>
          <div />
          <Num label="Search lower bound" value={searchLow} onChange={setSearchLow} step="0.01" />
          <Num label="Search upper bound" value={searchHigh} onChange={setSearchHigh} step="0.01" />
          <div />
          <Num label="Historical range low" value={histLow} onChange={setHistLow} step="0.01" />
          <Num label="Historical range high" value={histHigh} onChange={setHistHigh} step="0.01" />
          <div />
          <Num label="Peer range low" value={peerLow} onChange={setPeerLow} step="0.01" />
          <Num label="Peer range high" value={peerHigh} onChange={setPeerHigh} step="0.01" />
        </div>
      </Card>

      <button onClick={submit} disabled={loading} className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50">
        {loading ? "Solving…" : "Solve"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /scenarios/reverse-valuation (not bracketed within [search_low, search_high] → 400)" />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <MetricCard label={`Implied ${result.solve_target.replace("_", " ")}`} value={`${(result.implied_value * 100).toFixed(2)}%`} />
            <MetricCard label="Achieved EV" value={fmt(result.achieved_enterprise_value)} />
            <MetricCard label="Position in range" value={result.position.toFixed(2)} />
          </div>
          <Card title="Classification">
            <ClassificationBadge value={result.classification} />
            <p className="mt-3 text-xs text-[#111827]/60">
              Combined historical/peer span: {(result.historical_range.low * 100).toFixed(1)}%–
              {(result.historical_range.high * 100).toFixed(1)}% (historical),{" "}
              {(result.peer_range.low * 100).toFixed(1)}%–{(result.peer_range.high * 100).toFixed(1)}% (peer).
            </p>
          </Card>
        </>
      )}
    </div>
  );
}

function Num({ label, value, onChange, step = "1" }: { label: string; value: number; onChange: (v: number) => void; step?: string }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-[#111827]/60">{label}</label>
      <input type="number" step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums" />
    </div>
  );
}

function fmt(n: number): string {
  if (Math.abs(n) >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  return `$${n.toFixed(0)}`;
}
