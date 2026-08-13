"use client";

import { useState } from "react";
import { computeComps } from "@/lib/api";
import type { CompPeer, CompsInput, CompsResult, MultipleMetric } from "@/lib/types";
import { Card, ErrorState, MetricCard, SectionHeading, Table } from "@/components/ui";

const DEFAULT_PEERS: CompPeer[] = [
  { name: "Peer A", revenue: 80_000_000_000, ebitda: 22_000_000_000, ebit: 18_000_000_000, net_income: 14_000_000_000, market_cap: 300_000_000_000, debt: 20_000_000_000, cash: 15_000_000_000 },
  { name: "Peer B", revenue: 60_000_000_000, ebitda: 16_000_000_000, ebit: 13_000_000_000, net_income: 10_000_000_000, market_cap: 220_000_000_000, debt: 10_000_000_000, cash: 8_000_000_000 },
];

const METRICS: MultipleMetric[] = ["ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"];

export default function CompsPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [peers, setPeers] = useState<CompPeer[]>(DEFAULT_PEERS);
  const [targetRevenue, setTargetRevenue] = useState(100_000_000_000);
  const [targetEbitda, setTargetEbitda] = useState(28_000_000_000);
  const [targetEbit, setTargetEbit] = useState(24_000_000_000);
  const [targetNetIncome, setTargetNetIncome] = useState(18_000_000_000);
  const [targetDebt, setTargetDebt] = useState(15_000_000_000);
  const [targetCash, setTargetCash] = useState(20_000_000_000);
  const [shares, setShares] = useState(1_000_000_000);
  const [metric, setMetric] = useState<MultipleMetric>("ev_to_ebitda");
  const [multipleValue, setMultipleValue] = useState(12);

  const [result, setResult] = useState<CompsResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  function updatePeer(i: number, patch: Partial<CompPeer>) {
    setPeers((prev) => prev.map((p, idx) => (idx === i ? { ...p, ...patch } : p)));
  }

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    const payload: CompsInput = {
      peers,
      target_name: ticker,
      target_revenue: targetRevenue,
      target_ebitda: targetEbitda,
      target_ebit: targetEbit,
      target_net_income: targetNetIncome,
      target_debt: targetDebt,
      target_cash: targetCash,
      target_diluted_shares_outstanding: shares,
      chosen_multiple_metric: metric,
      chosen_multiple_value: multipleValue,
    };
    try {
      setResult(await computeComps(payload));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Comparable Companies"
        description={`Peer multiples and percentile stats for ${ticker}. Peer selection is out of scope for the deterministic engine — assemble the peer set here manually. Submits to POST /valuation/comps.`}
      />

      <Card title="Peer Set">
        <div className="space-y-3">
          {peers.map((p, i) => (
            <div key={i} className="grid grid-cols-2 gap-2 sm:grid-cols-8 items-center text-xs">
              <input className="rounded-[8px] border border-[#E5E7EB] px-2 py-1.5 col-span-2" value={p.name} onChange={(e) => updatePeer(i, { name: e.target.value })} placeholder="Name" />
              {(["revenue", "ebitda", "ebit", "net_income", "market_cap", "debt", "cash"] as const).map((f) => (
                <input
                  key={f}
                  type="number"
                  className="rounded-[8px] border border-[#E5E7EB] px-2 py-1.5 tabular-nums"
                  value={p[f] as number}
                  onChange={(e) => updatePeer(i, { [f]: Number(e.target.value) } as Partial<CompPeer>)}
                  placeholder={f}
                />
              ))}
            </div>
          ))}
          <button
            onClick={() =>
              setPeers((prev) => [...prev, { name: `Peer ${prev.length + 1}`, revenue: 0, ebitda: 0, ebit: 0, net_income: 0, market_cap: 0, debt: 0, cash: 0 }])
            }
            className="rounded-[8px] border border-[#2563EB]/30 px-3 py-1.5 text-xs font-medium text-[#2563EB] hover:bg-[#2563EB]/5"
          >
            + Add peer
          </button>
        </div>
      </Card>

      <Card title="Target & Chosen Multiple">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <Num label="Target revenue" value={targetRevenue} onChange={setTargetRevenue} />
          <Num label="Target EBITDA" value={targetEbitda} onChange={setTargetEbitda} />
          <Num label="Target EBIT" value={targetEbit} onChange={setTargetEbit} />
          <Num label="Target net income" value={targetNetIncome} onChange={setTargetNetIncome} />
          <Num label="Target debt" value={targetDebt} onChange={setTargetDebt} />
          <Num label="Target cash" value={targetCash} onChange={setTargetCash} />
          <Num label="Diluted shares outstanding" value={shares} onChange={setShares} />
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">Chosen multiple</label>
            <select value={metric} onChange={(e) => setMetric(e.target.value as MultipleMetric)} className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm">
              {METRICS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <Num label="Multiple value" value={multipleValue} onChange={setMultipleValue} step="0.1" />
        </div>
      </Card>

      <button onClick={submit} disabled={loading} className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50">
        {loading ? "Computing…" : "Run Comps"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /valuation/comps" />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <MetricCard label="Implied Enterprise Value" value={result.implied_enterprise_value != null ? fmt(result.implied_enterprise_value) : "N/A"} />
            <MetricCard label="Implied Equity Value" value={fmt(result.implied_equity_value)} tone="success" />
            <MetricCard label="Implied Price/Share" value={`$${result.implied_price_per_share.toFixed(2)}`} />
          </div>

          <Card title="Peer Multiples">
            <Table headers={["Peer", "EV/Rev", "EV/EBITDA", "EV/EBIT", "P/E", "P/B"]}>
              {result.peer_multiples.map((pm) => (
                <tr key={pm.name} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium">{pm.name}</td>
                  <td className="px-4 py-2.5 tabular-nums">{pm.ev_to_revenue?.toFixed(2) ?? "—"}</td>
                  <td className="px-4 py-2.5 tabular-nums">{pm.ev_to_ebitda?.toFixed(2) ?? "—"}</td>
                  <td className="px-4 py-2.5 tabular-nums">{pm.ev_to_ebit?.toFixed(2) ?? "—"}</td>
                  <td className="px-4 py-2.5 tabular-nums">{pm.price_to_earnings?.toFixed(2) ?? "—"}</td>
                  <td className="px-4 py-2.5 tabular-nums">{pm.price_to_book?.toFixed(2) ?? "—"}</td>
                </tr>
              ))}
            </Table>
          </Card>

          <Card title="Percentile Statistics">
            <Table headers={["Metric", "Min", "P25", "Median", "P75", "Max"]}>
              {result.stats.map((s) => (
                <tr key={s.metric} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium">{s.metric}</td>
                  <td className="px-4 py-2.5 tabular-nums">{s.min.toFixed(2)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{s.p25.toFixed(2)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{s.median.toFixed(2)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{s.p75.toFixed(2)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{s.max.toFixed(2)}</td>
                </tr>
              ))}
            </Table>
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
