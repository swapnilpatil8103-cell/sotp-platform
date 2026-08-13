"use client";

import { useState } from "react";
import { computeDcf } from "@/lib/api";
import type { DcfInput, DcfResult } from "@/lib/types";
import { Card, ErrorState, MetricCard, SectionHeading, Table } from "@/components/ui";
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

  const [result, setResult] = useState<DcfResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

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
        </div>
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
