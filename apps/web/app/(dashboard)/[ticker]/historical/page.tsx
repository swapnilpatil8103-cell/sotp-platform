"use client";

import { useEffect, useState } from "react";
import { getHistoricalTrends } from "@/lib/api";
import type { HistoricalTrendsRead } from "@/lib/types";
import {
  AIBadge,
  Card,
  ErrorState,
  MetricCard,
  PageSkeleton,
  ProvenanceBadge,
  SectionHeading,
  Table,
} from "@/components/ui";
import { MarginTrendChart, RevenueTrendChart } from "@/components/charts";

function pct(v?: number | null): string {
  return v == null ? "—" : `${v.toFixed(1)}%`;
}

function num(v?: number | null): string {
  return v == null ? "—" : v.toLocaleString();
}

export default function HistoricalTrendsPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [data, setData] = useState<HistoricalTrendsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getHistoricalTrends(ticker)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  if (loading) return <PageSkeleton />;
  if (error) return <ErrorState error={error} context={`Historical trends for ${ticker}`} />;
  if (!data) return null;

  const revenueYears = data.series["revenue"] ?? [];
  const marginYears = data.operating_margin_trend.years;
  const forward = data.suggested_forward_revenue;

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Historical Trends & Forward Suggestion"
        description={`${data.fiscal_period} · Fiscal years ${data.fiscal_years_covered.join(", ") || "—"} · Source: SEC XBRL company facts (REPORTED)`}
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <MetricCard
          label="Revenue CAGR"
          value={data.revenue_cagr.insufficient_history ? "—" : pct(data.revenue_cagr.cagr_pct)}
          sub={
            data.revenue_cagr.insufficient_history
              ? data.revenue_cagr.reason
              : `FY${data.revenue_cagr.start_year} → FY${data.revenue_cagr.end_year} (${data.revenue_cagr.num_years} yr)`
          }
          provenance="DERIVED"
        />
        <MetricCard
          label="Net Income CAGR"
          value={data.net_income_cagr.insufficient_history ? "—" : pct(data.net_income_cagr.cagr_pct)}
          sub={
            data.net_income_cagr.insufficient_history
              ? data.net_income_cagr.reason
              : `FY${data.net_income_cagr.start_year} → FY${data.net_income_cagr.end_year}`
          }
          provenance="DERIVED"
        />
        <MetricCard
          label="Latest Operating Margin"
          value={pct(data.operating_margin_trend.latest_margin_pct)}
          sub={
            data.operating_margin_trend.insufficient_history
              ? data.operating_margin_trend.reason
              : `Range ${pct(data.operating_margin_trend.min_margin_pct)} – ${pct(data.operating_margin_trend.max_margin_pct)}`
          }
          provenance="DERIVED"
        />
      </div>

      <Card title="Revenue History" subtitle="REPORTED annual revenue from SEC XBRL filings">
        <RevenueTrendChart years={revenueYears} />
      </Card>

      <Card title="Operating Margin Trend" subtitle="Operating income ÷ revenue, REPORTED years only">
        <MarginTrendChart years={marginYears} />
      </Card>

      <Card
        title="Suggested Forward Revenue"
        subtitle="Straight-line extrapolation of the historical CAGR — not a forecast"
        action={<AIBadge label="SUGGESTED" />}
      >
        {forward.insufficient_history ? (
          <p className="text-sm text-[#111827]/60">{forward.reason}</p>
        ) : (
          <>
            <p className="mb-3 text-xs text-[#111827]/60">{forward.basis}</p>
            <Table headers={["Fiscal Year", "Suggested Revenue", "Status"]}>
              {(forward.suggested_years ?? []).map((y, i) => (
                <tr key={y} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium text-[#111827]">{y}</td>
                  <td className="px-4 py-2.5 tabular-nums text-[#0B1F3A]">
                    {num(forward.suggested_values?.[i])}
                  </td>
                  <td className="px-4 py-2.5">
                    <ProvenanceBadge status="ESTIMATED" />
                  </td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </Card>

      <Card title="Full Series" subtitle="All tracked concepts, REPORTED/MISSING per fiscal year">
        <Table headers={["Concept", "Fiscal Year", "Value", "Status"]}>
          {Object.entries(data.series).flatMap(([concept, years]) =>
            years.map((y) => (
              <tr key={`${concept}-${y.fiscal_year}`} className="border-b border-[#E5E7EB] last:border-0">
                <td className="px-4 py-2.5 font-medium text-[#111827]">{concept}</td>
                <td className="px-4 py-2.5 text-[#111827]/60">{y.fiscal_year}</td>
                <td className="px-4 py-2.5 tabular-nums text-[#0B1F3A]">{num(y.value)}</td>
                <td className="px-4 py-2.5">
                  <ProvenanceBadge status={y.data_status} />
                </td>
              </tr>
            ))
          )}
        </Table>
      </Card>
    </div>
  );
}
