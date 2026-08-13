"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import type { SensitivityResult } from "@/lib/types";
import { Card, ErrorState, SectionHeading } from "@/components/ui";
import { SensitivityHeatmap } from "@/components/charts";

const BASE_INPUT = {
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

function range(center: number, step: number, n: number): number[] {
  const half = Math.floor(n / 2);
  return Array.from({ length: n }, (_, i) => Number((center + (i - half) * step).toFixed(4)));
}

/**
 * Sensitivity heatmap: WACC vs terminal growth rate, implied price/share.
 * Backend previously had no router exposing backend.valuation.sensitivity —
 * a minimal POST /valuation/sensitivity/dcf endpoint was added in this
 * phase (see backend/api/routers/valuation.py) purely to expose the
 * existing pure-function sensitivity engine; no math changed.
 */
export default function SensitivitiesPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [result, setResult] = useState<SensitivityResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    setError(null);
    setResult(null);
    const base = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";
    try {
      const res = await fetch(`${base}/valuation/sensitivity/dcf`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_dcf_input: BASE_INPUT,
          row_field: "wacc",
          row_values: range(0.09, 0.01, 5),
          col_field: "terminal_growth_rate",
          col_values: range(0.025, 0.005, 5),
          output_field: "implied_price_per_share",
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Sensitivity request failed", body.detail);
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
        title="Sensitivities"
        description={`Implied price/share for ${ticker} across a WACC vs. terminal growth rate grid. Submits to POST /valuation/sensitivity/dcf (backend.valuation.sensitivity.dcf_sensitivity re-runs the DCF engine once per grid cell).`}
      />

      <button
        onClick={run}
        disabled={loading}
        className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50"
      >
        {loading ? "Building grid…" : "Build Sensitivity Grid"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /valuation/sensitivity/dcf" />}

      {result && (
        <Card title="WACC vs. Terminal Growth Rate — Implied Price/Share">
          <SensitivityHeatmap result={result} />
        </Card>
      )}
    </div>
  );
}
