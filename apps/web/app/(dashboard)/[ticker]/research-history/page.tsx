"use client";

import { useEffect, useState } from "react";
import { diffCompanyRuns, getCompanyId, listCompanyRuns } from "@/lib/api";
import type { DiffResponse, ValuationRunOut } from "@/lib/types";
import { Card, ErrorState, SectionHeading, Table } from "@/components/ui";

/**
 * Research History: valuation run version list + diff viewer, backed by
 * Phase 7's GET /valuation/company/{company_id}/runs and
 * GET /valuation/company/{company_id}/runs/diff.
 *
 * company_id is resolved automatically from the ticker via
 * GET /companies/{ticker}/id (closes the Phase 10 audit gap — no more
 * manual company_id entry).
 */
export default function ResearchHistoryPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [companyId, setCompanyId] = useState<number | "">("");
  const [resolveError, setResolveError] = useState<unknown>(null);
  const [runs, setRuns] = useState<ValuationRunOut[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const [fromVersion, setFromVersion] = useState<number | "">("");
  const [toVersion, setToVersion] = useState<number | "">("");
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [diffError, setDiffError] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    setResolveError(null);
    setCompanyId("");
    getCompanyId(ticker)
      .then((r) => !cancelled && setCompanyId(r.company_id))
      .catch((err) => !cancelled && setResolveError(err));
    return () => {
      cancelled = true;
    };
  }, [ticker]);

  useEffect(() => {
    if (companyId !== "") {
      loadRuns();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  async function loadRuns() {
    if (companyId === "") return;
    setLoading(true);
    setError(null);
    setRuns(null);
    try {
      const r = await listCompanyRuns(Number(companyId));
      setRuns(r);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  async function loadDiff() {
    if (companyId === "" || fromVersion === "" || toVersion === "") return;
    setDiffError(null);
    setDiff(null);
    try {
      const d = await diffCompanyRuns(Number(companyId), Number(fromVersion), Number(toVersion));
      setDiff(d);
    } catch (err) {
      setDiffError(err);
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Research History"
        description={`Versioned valuation runs for ${ticker} and their diffs (Phase 7 governance layer).`}
      />

      {Boolean(resolveError) && (
        <ErrorState error={resolveError} context="GET /companies/{ticker}/id — could not resolve company_id" />
      )}

      {companyId !== "" && (
        <Card>
          <p className="text-xs text-[#111827]/50">
            Resolved company_id <span className="font-semibold tabular-nums text-[#111827]/80">{companyId}</span> for {ticker}
          </p>
        </Card>
      )}

      {Boolean(error) && <ErrorState error={error} context="GET /valuation/company/{id}/runs" />}

      {runs && (
        <Card title={`Runs (${runs.length})`}>
          {runs.length === 0 ? (
            <p className="text-sm text-[#111827]/50">No valuation runs recorded for this company yet.</p>
          ) : (
            <Table headers={["Version", "Method", "Created", "Created By"]}>
              {runs.map((r) => (
                <tr key={r.id} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium tabular-nums">{r.version}</td>
                  <td className="px-4 py-2.5">{r.method}</td>
                  <td className="px-4 py-2.5 text-[#111827]/60">{new Date(r.created_at).toLocaleString()}</td>
                  <td className="px-4 py-2.5 text-[#111827]/60">{r.created_by ?? "—"}</td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      )}

      <Card title="Diff Two Versions">
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="number"
            value={fromVersion}
            onChange={(e) => setFromVersion(e.target.value === "" ? "" : Number(e.target.value))}
            placeholder="from version"
            className="w-32 rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
          />
          <span className="text-[#111827]/40">→</span>
          <input
            type="number"
            value={toVersion}
            onChange={(e) => setToVersion(e.target.value === "" ? "" : Number(e.target.value))}
            placeholder="to version"
            className="w-32 rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums"
          />
          <button
            onClick={loadDiff}
            className="rounded-[10px] border border-[#2563EB]/30 px-4 py-2 text-sm font-medium text-[#2563EB] hover:bg-[#2563EB]/5"
          >
            Diff
          </button>
        </div>

        {Boolean(diffError) && <div className="mt-4"><ErrorState error={diffError} context="GET /valuation/company/{id}/runs/diff" /></div>}

        {diff && (
          <div className="mt-4 space-y-3">
            <p className="text-sm text-[#111827]/70">{diff.summary}</p>
            <Table headers={["Field", "From", "To"]}>
              {diff.changed_fields.map((f: any, i: number) => (
                <tr key={i} className="border-b border-[#E5E7EB] last:border-0">
                  <td className="px-4 py-2.5 font-medium">{f.field ?? JSON.stringify(f)}</td>
                  <td className="px-4 py-2.5 tabular-nums text-[#DC2626]">{String(f.from ?? f.old ?? "—")}</td>
                  <td className="px-4 py-2.5 tabular-nums text-[#16A34A]">{String(f.to ?? f.new ?? "—")}</td>
                </tr>
              ))}
            </Table>
            <p className="text-xs text-[#111827]/40">{diff.unchanged_field_count} unchanged fields</p>
          </div>
        )}
      </Card>
    </div>
  );
}
