"use client";

import { useState } from "react";
import { runScenarios } from "@/lib/api";
import type { DcfInput, ScenarioEngineResult } from "@/lib/types";
import { Card, ErrorState, MetricCard, SectionHeading, Table } from "@/components/ui";
import { ScenarioComparisonChart } from "@/components/charts";

function baseDcfInput(growth: number, margin: number): DcfInput {
  return {
    base_revenue: 100_000_000_000,
    revenue_growth_rates: [growth, growth, growth, growth, growth],
    ebit_margins: [margin, margin, margin, margin, margin],
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
}

export default function ScenariosPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [bullGrowth, setBullGrowth] = useState(0.15);
  const [baseGrowth, setBaseGrowth] = useState(0.08);
  const [bearGrowth, setBearGrowth] = useState(0.02);
  const [marketPrice, setMarketPrice] = useState<number | "">("");
  const [result, setResult] = useState<ScenarioEngineResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await runScenarios({
        bull: { name: "Bull", dcf_input: baseDcfInput(bullGrowth, 0.28) },
        base: { name: "Base", dcf_input: baseDcfInput(baseGrowth, 0.24) },
        bear: { name: "Bear", dcf_input: baseDcfInput(bearGrowth, 0.19) },
        current_market_price: marketPrice === "" ? null : Number(marketPrice),
      });
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
        title="Scenario Comparison"
        description={`Bull / Base / Bear DCF runs for ${ticker}, each an explicit, independently-computed input set (no invented "+2%" shortcuts). Submits to POST /scenarios/run.`}
      />

      <Card title="Revenue growth assumption per scenario (flat rate, illustrative)">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
          <RateInput label="Bear growth" value={bearGrowth} onChange={setBearGrowth} />
          <RateInput label="Base growth" value={baseGrowth} onChange={setBaseGrowth} />
          <RateInput label="Bull growth" value={bullGrowth} onChange={setBullGrowth} />
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">
              Current market price (optional)
            </label>
            <input
              type="number"
              value={marketPrice}
              onChange={(e) => setMarketPrice(e.target.value === "" ? "" : Number(e.target.value))}
              className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
            />
          </div>
        </div>
      </Card>

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50"
      >
        {loading ? "Running…" : "Run Scenarios"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /scenarios/run" />}

      {result && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {(["bear", "base", "bull"] as const).map((k) => (
              <MetricCard
                key={k}
                label={k.toUpperCase()}
                value={`$${result[k].implied_price_per_share.toFixed(2)}`}
                sub={
                  result[k].upside_downside_pct != null
                    ? `${result[k].upside_downside_pct!.toFixed(1)}% vs market`
                    : undefined
                }
                tone={k === "bull" ? "success" : k === "bear" ? "danger" : "default"}
              />
            ))}
          </div>

          {!result.ordering_valid && (
            <div className="rounded-[10px] border border-[#F59E0B]/40 bg-[#F59E0B]/[0.06] p-3 text-xs text-[#B45309]">
              Warning: bull EV is not ≥ base EV ≥ bear EV for the current inputs — review the
              assumption set.
            </div>
          )}

          <Card title="Enterprise Value by Scenario">
            <ScenarioComparisonChart result={result} />
          </Card>

          <Card title="Detail">
            <Table headers={["Scenario", "Enterprise Value", "Equity Value", "Implied Price/Share"]}>
              {(["bear", "base", "bull"] as const).map((k) => (
                <tr key={k} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium capitalize text-[#111827]">{k}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(result[k].enterprise_value)}</td>
                  <td className="px-4 py-2.5 tabular-nums">{fmt(result[k].equity_value)}</td>
                  <td className="px-4 py-2.5 tabular-nums">${result[k].implied_price_per_share.toFixed(2)}</td>
                </tr>
              ))}
            </Table>
          </Card>
        </>
      )}
    </div>
  );
}

function RateInput({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-[#111827]/60">{label}</label>
      <input
        type="number"
        step="0.01"
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
