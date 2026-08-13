"use client";

import { useEffect, useState } from "react";
import { getCompanySegments } from "@/lib/api";
import type { CompanySegmentsRead } from "@/lib/types";
import {
  Card,
  ErrorState,
  PageSkeleton,
  ProvenanceBadge,
  SectionHeading,
  Table,
} from "@/components/ui";

const METRIC_ORDER = ["revenue", "operating_income", "da", "capex", "assets"];
const METRIC_LABEL: Record<string, string> = {
  revenue: "Revenue",
  operating_income: "Operating Income",
  da: "D&A",
  capex: "CapEx",
  assets: "Assets",
};

export default function SegmentsPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [data, setData] = useState<CompanySegmentsRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getCompanySegments(ticker)
      .then((d) => !cancelled && setData(d))
      .catch((err) => !cancelled && setError(err))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  if (loading) return <PageSkeleton />;
  if (error) return <ErrorState error={error} context={`Segment data for ${ticker}`} />;
  if (!data) return null;

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Segment Breakdown"
        description={`Fiscal ${data.fiscal_year} ${data.fiscal_period} · Overall coverage ${data.overall_coverage_pct.toFixed(0)}% · Source: latest 10-K inline XBRL`}
      />

      {data.note && (
        <Card className="border-[#F59E0B]/40 bg-[#F59E0B]/[0.05]">
          <p className="text-sm text-[#B45309]">{data.note}</p>
        </Card>
      )}

      {data.segments.length === 0 ? (
        <Card>
          <p className="text-sm text-[#111827]/60">
            No segment-dimensional facts were found in this filing. This company may not
            disclose reportable segments, or the filing did not tag segment axes in its
            inline XBRL.
          </p>
        </Card>
      ) : (
        data.segments.map((seg) => (
          <Card key={seg.name} title={seg.name} subtitle={`Coverage ${seg.overall_coverage_pct.toFixed(0)}%`}>
            <Table headers={["Concept", "Period", "Value", "Status", "Source Tag"]}>
              {METRIC_ORDER.filter((m) => seg.facts.some((f) => f.concept === m)).map((concept) =>
                seg.facts
                  .filter((f) => f.concept === concept)
                  .map((f, i) => (
                    <tr key={`${concept}-${i}`} className="border-b border-[#E5E7EB] last:border-0">
                      <td className="px-4 py-2.5 font-medium text-[#111827]">
                        {METRIC_LABEL[f.concept] ?? f.concept}
                      </td>
                      <td className="px-4 py-2.5 text-[#111827]/60">{f.period}</td>
                      <td className="px-4 py-2.5 tabular-nums text-[#0B1F3A]">
                        {f.value != null ? f.value.toLocaleString() : "—"}
                      </td>
                      <td className="px-4 py-2.5">
                        <ProvenanceBadge status={f.data_status} />
                      </td>
                      <td className="px-4 py-2.5 text-xs text-[#111827]/40">
                        {f.xbrl_tag ?? "—"}
                      </td>
                    </tr>
                  ))
              )}
            </Table>

            <div className="mt-4 flex flex-wrap gap-2">
              {seg.coverage.map((c) => (
                <span
                  key={c.metric}
                  className="rounded-[6px] border border-[#E5E7EB] bg-white px-2.5 py-1 text-xs text-[#111827]/70"
                >
                  {METRIC_LABEL[c.metric] ?? c.metric}:{" "}
                  <span className="font-semibold text-[#0B1F3A]">
                    {c.coverage_pct.toFixed(0)}%
                  </span>{" "}
                  ({c.reported_periods}/{c.total_periods})
                </span>
              ))}
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
