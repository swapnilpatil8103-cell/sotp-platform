"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getCompany,
  getCompanyFilings,
  getCompanySegments,
  getMarketData,
} from "@/lib/api";
import type {
  CompanyRead,
  CompanySegmentsRead,
  FilingsResponse,
  MarketDataRead,
} from "@/lib/types";
import {
  Card,
  ErrorState,
  MetricCard,
  PageSkeleton,
  ProvenanceBadge,
  SectionHeading,
} from "@/components/ui";
import { SegmentValuationChart } from "@/components/charts";

export default function OverviewPage({
  params,
}: {
  params: { ticker: string };
}) {
  const ticker = params.ticker.toUpperCase();
  const [company, setCompany] = useState<CompanyRead | null>(null);
  const [market, setMarket] = useState<MarketDataRead | null>(null);
  const [filings, setFilings] = useState<FilingsResponse | null>(null);
  const [segments, setSegments] = useState<CompanySegmentsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [marketError, setMarketError] = useState<unknown>(null);
  const [segmentsError, setSegmentsError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const c = await getCompany(ticker);
        if (cancelled) return;
        setCompany(c);
      } catch (err) {
        if (!cancelled) setError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }

      try {
        const m = await getMarketData(ticker);
        if (!cancelled) setMarket(m);
      } catch (err) {
        if (!cancelled) setMarketError(err);
      }

      try {
        const f = await getCompanyFilings(ticker);
        if (!cancelled) setFilings(f);
      } catch {
        /* filings are supplementary, ignore quietly */
      }

      try {
        const s = await getCompanySegments(ticker);
        if (!cancelled) setSegments(s);
      } catch (err) {
        if (!cancelled) setSegmentsError(err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  if (loading) return <PageSkeleton />;
  if (error) return <ErrorState error={error} context={`Loading company data for ${ticker}`} />;

  const latestFiling = filings?.filings?.[0];
  const coveragePct = segments?.overall_coverage_pct;

  const segmentChartData = (segments?.segments ?? []).map((s) => {
    const rev = s.facts.find((f) => f.concept === "revenue" && f.value != null);
    return { name: s.name, enterprise_value: rev?.value ?? 0 };
  });

  return (
    <div className="space-y-8">
      <div>
        <p className="text-xs uppercase tracking-wide text-[#2563EB] font-medium">
          {company?.exchange ?? "—"} · {company?.sic_description ?? "Sector unclassified"}
        </p>
        <h1 className="mt-1 text-2xl font-semibold text-[#0B1F3A]">
          {company?.name ?? ticker} ({ticker})
        </h1>
        <p className="mt-1 text-sm text-[#111827]/50">
          CIK {company?.cik} · Fiscal year end {company?.fiscal_year_end ?? "unknown"}
        </p>
      </div>

      <section>
        <SectionHeading title="Snapshot" description="Latest available price/coverage data, plus SOTP/AI valuation summary once a valuation run has been executed." />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          <MetricCard
            label="Current Price"
            value={market?.price != null ? `$${market.price.toFixed(2)}` : marketError ? "N/A" : "—"}
            sub={market?.currency ?? undefined}
            provenance={market ? "REPORTED" : undefined}
          />
          <MetricCard
            label="Market Cap"
            value={market?.market_cap != null ? fmtLarge(market.market_cap) : "—"}
            provenance={market ? "REPORTED" : undefined}
          />
          <MetricCard
            label="Latest Filing"
            value={latestFiling ? latestFiling.form : "—"}
            sub={latestFiling?.filing_date ?? undefined}
          />
          <MetricCard
            label="Data Coverage"
            value={coveragePct != null ? `${coveragePct.toFixed(0)}%` : segmentsError ? "N/A" : "—"}
            tone={coveragePct != null ? (coveragePct >= 85 ? "success" : coveragePct >= 60 ? "warning" : "danger") : "default"}
          />
          <MetricCard
            label="AI-Assisted Fair Value"
            value="Pending run"
            sub="Requires an approved valuation run (Research History)"
          />
          <MetricCard
            label="SOTP Fair Value"
            value="Pending run"
            sub="See SOTP tab to compute"
          />
          <MetricCard
            label="Upside / Downside"
            value="—"
            sub="Computed once a valuation run exists"
          />
          <MetricCard
            label="Conglomerate Discount"
            value="—"
            sub="Computed on the SOTP tab"
          />
        </div>
      </section>

      {Boolean(marketError) && (
        <ErrorState error={marketError} context="Market data" />
      )}

      <section>
        <SectionHeading
          title="Segment Valuation Breakdown"
          description="Segment revenue as extracted from the latest 10-K's inline XBRL (proxy view — full segment EV requires a DCF/comps run per segment on the Segments tab)."
        />
        <Card>
          {segmentsError ? (
            <ErrorState error={segmentsError} context="Segment data" />
          ) : (
            <SegmentValuationChart data={segmentChartData} />
          )}
        </Card>
      </section>
    </div>
  );
}

function fmtLarge(n: number): string {
  if (Math.abs(n) >= 1_000_000_000_000) return `$${(n / 1_000_000_000_000).toFixed(2)}T`;
  if (Math.abs(n) >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  return `$${n.toFixed(0)}`;
}
