"use client";

import { useState } from "react";
import { computeDcf, computePeerInformedBeta } from "@/lib/api";
import type { BetaAnalysisResult, DcfInput, DcfResult, PeerBetaInputRow } from "@/lib/types";
import { AIBadge, Card, ErrorState, MetricCard, SectionHeading, Table } from "@/components/ui";
import { DcfProjectionChart } from "@/components/charts";

const N_YEARS = 5;

function repeat(v: number): number[] {
  return Array.from({ length: N_YEARS }, () => v);
}

export default function DcfPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [baseRevenue, setBaseRevenue] = useState(100_000_000_000);
  const [growth, setGrowth] = useState(0.08);
  const [ebitMargin, setEbitMargin] = useState(0.24);
  const [taxRate, setTaxRate] = useState(0.21);
  const [daPct, setDaPct] = useState(0.04);
  const [capexPct, setCapexPct] = useState(0.05);
  const [nwcPct, setNwcPct] = useState(0.01);
  const [wacc, setWacc] = useState(0.09);
  const [terminalGrowth, setTerminalGrowth] = useState(0.025);
  const [netDebt, setNetDebt] = useState(5_000_000_000);
  const [cash, setCash] = useState(20_000_000_000);
  const [shares, setShares] = useState(1_000_000_000);
  const [exitMultiple, setExitMultiple] = useState<number | "">("");

  const [result, setResult] = useState<DcfResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  // Peer-informed beta (SUGGESTED, pre-fill only) -- POST /valuation/wacc/peer-beta.
  const [beta, setBeta] = useState(1.1);
  const [targetDE, setTargetDE] = useState(0.4);
  const [targetTax, setTargetTax] = useState(0.21);
  const [peerBetaResult, setPeerBetaResult] = useState<BetaAnalysisResult | null>(null);
  const [peerBetaLoading, setPeerBetaLoading] = useState(false);
  const [peerBetaError, setPeerBetaError] = useState<unknown>(null);
  const [betaIsSuggested, setBetaIsSuggested] = useState(false);

  // Placeholder peer set for the demo toggle -- in a full integration this
  // would come from GET /companies/{ticker}/peer-candidates real financials.
  const DEMO_PEERS: PeerBetaInputRow[] = [
    { name: "Peer A", levered_beta: 1.3, debt_to_equity: 0.4, tax_rate: 0.25 },
    { name: "Peer B", levered_beta: 1.1, debt_to_equity: 0.3, tax_rate: 0.23 },
    { name: "Peer C", levered_beta: 1.4, debt_to_equity: 0.5, tax_rate: 0.24 },
  ];

  async function usePeerInformedBeta() {
    setPeerBetaLoading(true);
    setPeerBetaError(null);
    try {
      const r = await computePeerInformedBeta({
        peers: DEMO_PEERS,
        aggregation: "median",
        target_debt_to_equity: targetDE,
        target_tax_rate: targetTax,
      });
      setPeerBetaResult(r);
      setBeta(r.relevered_beta);
      setBetaIsSuggested(true);
    } catch (err) {
      setPeerBetaError(err);
    } finally {
      setPeerBetaLoading(false);
    }
  }

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    const payload: DcfInput = {
      base_revenue: baseRevenue,
      revenue_growth_rates: repeat(growth),
      ebit_margins: repeat(ebitMargin),
      tax_rate: taxRate,
      da_pct_of_revenue: repeat(daPct),
      capex_pct_of_revenue: repeat(capexPct),
      nwc_change_pct_of_revenue: repeat(nwcPct),
      wacc,
      terminal_growth_rate: terminalGrowth,
      net_debt: netDebt,
      cash_and_equivalents: cash,
      investments: 0,
      minority_interest: 0,
      diluted_shares_outstanding: shares,
      exit_multiple: exitMultiple === "" ? null : Number(exitMultiple),
    };
    try {
      setResult(await computeDcf(payload));
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Discounted Cash Flow"
        description={`5-year FCFF projection for ${ticker}, Gordon Growth terminal value. Submits to POST /valuation/dcf (deterministic, backend.valuation.dcf).`}
      />

      <Card title="Assumptions (flat rate across the 5-year forecast, for simplicity)">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <Num label="Base revenue" value={baseRevenue} onChange={setBaseRevenue} />
          <Num label="Revenue growth" value={growth} onChange={setGrowth} step="0.01" />
          <Num label="EBIT margin" value={ebitMargin} onChange={setEbitMargin} step="0.01" />
          <Num label="Tax rate" value={taxRate} onChange={setTaxRate} step="0.01" />
          <Num label="D&A % of revenue" value={daPct} onChange={setDaPct} step="0.01" />
          <Num label="CapEx % of revenue" value={capexPct} onChange={setCapexPct} step="0.01" />
          <Num label="NWC change % of revenue" value={nwcPct} onChange={setNwcPct} step="0.01" />
          <Num label="WACC" value={wacc} onChange={setWacc} step="0.005" />
          <Num label="Terminal growth rate" value={terminalGrowth} onChange={setTerminalGrowth} step="0.005" />
          <Num label="Net debt" value={netDebt} onChange={setNetDebt} />
          <Num label="Cash & equivalents" value={cash} onChange={setCash} />
          <Num label="Diluted shares outstanding" value={shares} onChange={setShares} />
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">
              Exit multiple (EV/EBITDA, optional)
            </label>
            <input
              type="number"
              step="0.5"
              value={exitMultiple}
              onChange={(e) => setExitMultiple(e.target.value === "" ? "" : Number(e.target.value))}
              className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
              placeholder="e.g. 10"
            />
            <p className="mt-1 text-[10px] text-[#111827]/40">
              When set, an independent Exit Multiple terminal value is computed alongside Gordon Growth.
            </p>
          </div>
        </div>
      </Card>

      <Card
        title="WACC — Beta"
        subtitle="Levered beta feeding CAPM cost of equity. Optionally pre-fill it with a peer-informed unlevered/relevered beta — reviewable, never forced."
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <div>
            <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-[#111827]/60">
              Beta {betaIsSuggested && <AIBadge label="Suggested" />}
            </label>
            <input
              type="number"
              step="0.01"
              value={beta}
              onChange={(e) => {
                setBeta(Number(e.target.value));
                setBetaIsSuggested(false);
              }}
              className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
            />
          </div>
          <Num label="Target D/E" value={targetDE} onChange={setTargetDE} step="0.01" />
          <Num label="Target tax rate" value={targetTax} onChange={setTargetTax} step="0.01" />
          <div className="flex items-end">
            <button
              onClick={usePeerInformedBeta}
              disabled={peerBetaLoading}
              className="w-full rounded-[8px] border border-[#2563EB]/40 bg-[#0B1F3A] px-3 py-2 text-xs font-medium text-white hover:bg-[#0B1F3A]/90 disabled:opacity-50"
            >
              {peerBetaLoading ? "Computing…" : "Use peer-informed beta"}
            </button>
          </div>
        </div>
        {Boolean(peerBetaError) && <div className="mt-3"><ErrorState error={peerBetaError} context="POST /valuation/wacc/peer-beta" /></div>}
        {peerBetaResult && (
          <p className="mt-3 text-xs text-[#111827]/60">
            Unlevered ({peerBetaResult.aggregation}) beta across {peerBetaResult.used_peers.length} peer(s):{" "}
            {peerBetaResult.unlevered_beta_aggregate.toFixed(3)}. Relevered at target D/E {peerBetaResult.target_debt_to_equity}
            , tax {peerBetaResult.target_tax_rate}: <strong>{peerBetaResult.relevered_beta.toFixed(3)}</strong> — pre-filled
            above, still editable.
            {peerBetaResult.skipped_peers.length > 0 && (
              <>
                {" "}
                Skipped: {peerBetaResult.skipped_peers.map((p) => `${p.name} (${p.reason})`).join("; ")}
              </>
            )}
          </p>
        )}
      </Card>

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50"
      >
        {loading ? "Computing…" : "Run DCF"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /valuation/dcf" />}

      {result && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <MetricCard label="Enterprise Value" value={fmt(result.enterprise_value)} />
            <MetricCard label="Equity Value" value={fmt(result.equity_value)} tone="success" />
            <MetricCard label="Implied Price/Share" value={`$${result.implied_price_per_share.toFixed(2)}`} />
            <MetricCard label="Terminal Value (PV)" value={fmt(result.terminal_value_discounted)} />
          </div>

          {result.exit_multiple_enterprise_value != null && (
            <Card
              title="Exit Multiple Terminal Value (alongside Gordon Growth)"
              subtitle={`Terminal-year EBITDA ${fmt(result.terminal_year_ebitda ?? 0)} × exit multiple ${
                result.inputs.exit_multiple ?? ""
              }`}
            >
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <MetricCard label="Exit-Multiple EV" value={fmt(result.exit_multiple_enterprise_value)} />
                <MetricCard label="Exit-Multiple Equity Value" value={fmt(result.exit_multiple_equity_value ?? 0)} />
                <MetricCard
                  label="Exit-Multiple Price/Share"
                  value={`$${(result.exit_multiple_implied_price_per_share ?? 0).toFixed(2)}`}
                />
                <MetricCard
                  label="Exit-Multiple TV (PV)"
                  value={fmt(result.exit_multiple_terminal_value_discounted ?? 0)}
                />
              </div>
              <p className="mt-3 text-xs text-[#111827]/50">
                Independent of the Gordon Growth result above — both terminal value methods are computed from
                the same explicit forecast, shown side by side for comparison.
              </p>
            </Card>
          )}

          <Card title="FCFF Projection">
            <DcfProjectionChart projections={result.projections} />
          </Card>

          <Card title="Year-by-Year Detail">
            <Table headers={["Year", "Revenue", "EBIT", "NOPAT", "FCFF", "Discounted FCFF"]}>
              {result.projections.map((p) => (
                <tr key={p.year_index} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium">{p.year_index}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(p.revenue)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(p.ebit)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(p.nopat)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(p.fcff)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(p.discounted_fcff)}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

function Num({
  label,
  value,
  onChange,
  step = "1",
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  step?: string;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-[#111827]/60">{label}</label>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
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
