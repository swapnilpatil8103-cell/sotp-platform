"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import type { RiskDashboardResult } from "@/lib/types";
import { AIBadge, Card, ErrorState, RiskBadge, SectionHeading } from "@/components/ui";

const CATEGORY_KEYS: (keyof RiskDashboardResult)[] = [
  "data_risk",
  "model_risk",
  "forecast_risk",
  "market_risk",
  "strategic_risk",
  "execution_risk",
];

export default function RisksPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [coverage, setCoverage] = useState(70);
  const [methodologies, setMethodologies] = useState(1);
  const [spread, setSpread] = useState(25);
  const [classification, setClassification] = useState<"CONSERVATIVE" | "REASONABLE" | "AGGRESSIVE" | "EXTREME">("REASONABLE");
  const [beta, setBeta] = useState<number | "">(1.1);
  const [volatility, setVolatility] = useState<number | "">(35);
  const [result, setResult] = useState<RiskDashboardResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function submit() {
    setLoading(true);
    setError(null);
    setResult(null);
    const base = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";
    const qp = new URLSearchParams({
      coverage_pct: String(coverage),
      methodologies_used: String(methodologies),
      sensitivity_spread_pct: String(spread),
      forecast_classification: classification,
      ...(beta !== "" ? { beta: String(beta) } : {}),
      ...(volatility !== "" ? { volatility_pct: String(volatility) } : {}),
    });
    try {
      const res = await fetch(`${base}/scenarios/risk-dashboard?${qp.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Risk dashboard request failed", body.detail);
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
        title="Risk Dashboard"
        description={`Six risk categories for ${ticker}. Data/Model/Forecast/Market are rule-based; Strategic and Execution risk are AI-assisted (constrained to LOW/MEDIUM/HIGH — never free text). Submits to POST /scenarios/risk-dashboard.`}
      />

      <Card title="Inputs">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <NumInput label="Segment coverage %" value={coverage} onChange={setCoverage} />
          <NumInput label="Methodologies used" value={methodologies} onChange={setMethodologies} />
          <NumInput label="Sensitivity spread %" value={spread} onChange={setSpread} />
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">
              Forecast classification
            </label>
            <select
              value={classification}
              onChange={(e) => setClassification(e.target.value as any)}
              className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm"
            >
              {["CONSERVATIVE", "REASONABLE", "AGGRESSIVE", "EXTREME"].map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <NumInput label="Beta (optional)" value={beta} onChange={setBeta} optional />
          <NumInput label="1y volatility % (optional)" value={volatility} onChange={setVolatility} optional />
        </div>
      </Card>

      <button
        onClick={submit}
        disabled={loading}
        className="rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50"
      >
        {loading ? "Scoring…" : "Run Risk Dashboard"}
      </button>

      {Boolean(error) && <ErrorState error={error} context="POST /scenarios/risk-dashboard (requires GEMINI_API_KEY configured for the AI-assisted categories)" />}

      {result && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {CATEGORY_KEYS.map((key) => {
            const c = result[key];
            return (
              <Card key={key}>
                <div className="flex items-center justify-between">
                  <p className="text-sm font-semibold text-[#0B1F3A]">{c.category}</p>
                  <RiskBadge level={c.score} />
                </div>
                {c.ai_assisted && (
                  <div className="mt-2">
                    <AIBadge />
                  </div>
                )}
                <p className="mt-2 text-xs text-[#111827]/60">{c.explanation}</p>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

function NumInput({
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
