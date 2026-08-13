"use client";

import { useEffect, useState } from "react";
import { ApiError, getCompanyId } from "@/lib/api";
import type { AssumptionDecisionOut, ValueUnlockProposeResponse } from "@/lib/types";
import { AIPanel, Card, ErrorState, SectionHeading, Table } from "@/components/ui";

const BASE = () => process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

/**
 * Value Unlock: AI proposes which structural actions (spin-off, IPO, asset
 * sale, buyback, debt reduction, special dividend) might be worth modeling
 * -- each proposal becomes a PENDING AssumptionDecision that a human must
 * approve or reject before any dollar figure is computed (Phase 7/8
 * governance pattern). This page wires the full propose -> approve/reject ->
 * compute loop against the real endpoints. company_id is resolved
 * automatically from the ticker via GET /companies/{ticker}/id.
 */
export default function ValueUnlockPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [companyId, setCompanyId] = useState<number | "">("");
  const [resolveError, setResolveError] = useState<unknown>(null);

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

  const [sotpBreakdownJson, setSotpBreakdownJson] = useState(
    JSON.stringify({ "Segment A": 50_000_000_000, "Segment B": 20_000_000_000 }, null, 2)
  );
  const [discount, setDiscount] = useState<number | "">(15);

  const [proposeResult, setProposeResult] = useState<ValueUnlockProposeResponse | null>(null);
  const [proposeError, setProposeError] = useState<unknown>(null);
  const [proposing, setProposing] = useState(false);

  const [decisions, setDecisions] = useState<Record<number, AssumptionDecisionOut>>({});
  const [decisionErrors, setDecisionErrors] = useState<Record<number, unknown>>({});

  async function propose() {
    setProposing(true);
    setProposeError(null);
    setProposeResult(null);
    try {
      let sotpBreakdown: unknown;
      try {
        sotpBreakdown = JSON.parse(sotpBreakdownJson);
      } catch {
        throw new Error("SOTP breakdown must be valid JSON (e.g. {\"Segment A\": 50000000000}).");
      }
      const qp = new URLSearchParams({
        company_id: String(companyId),
        ...(discount !== "" ? { conglomerate_discount_pct: String(discount) } : {}),
      });
      const res = await fetch(`${BASE()}/scenarios/value-unlock/propose?${qp.toString()}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sotpBreakdown),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Propose failed", body.detail);
      }
      setProposeResult(await res.json());
    } catch (err) {
      setProposeError(err);
    } finally {
      setProposing(false);
    }
  }

  async function decide(decisionId: number, decision: "APPROVE" | "REJECT") {
    try {
      const res = await fetch(`${BASE()}/valuation/assumptions/${decisionId}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, user_id: "analyst:web-ui", reason: `${decision} via Value Unlock UI` }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Decision failed", body.detail);
      }
      const out: AssumptionDecisionOut = await res.json();
      setDecisions((prev) => ({ ...prev, [decisionId]: out }));
    } catch (err) {
      setDecisionErrors((prev) => ({ ...prev, [decisionId]: err }));
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="Value Unlock"
        description={`AI-proposed structural actions for ${ticker} (spin-off, IPO, asset sale, buyback, debt reduction, special dividend) — each requires human approval before any dollar figure is computed.`}
      />

      {Boolean(resolveError) && (
        <ErrorState error={resolveError} context="GET /companies/{ticker}/id — could not resolve company_id" />
      )}

      <Card title="Propose Ideas">
        <p className="mb-3 text-xs text-[#111827]/50">
          company_id{" "}
          <span className="font-semibold tabular-nums text-[#111827]/80">{companyId === "" ? "resolving…" : companyId}</span>{" "}
          for {ticker}
        </p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-[#111827]/60">Conglomerate discount % (optional)</label>
            <input type="number" value={discount} onChange={(e) => setDiscount(e.target.value === "" ? "" : Number(e.target.value))} className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 text-sm tabular-nums" />
          </div>
        </div>
        <div className="mt-4">
          <label className="mb-1 block text-xs font-medium text-[#111827]/60">SOTP breakdown (JSON, segment → attributed EV)</label>
          <textarea
            value={sotpBreakdownJson}
            onChange={(e) => setSotpBreakdownJson(e.target.value)}
            rows={5}
            className="w-full rounded-[8px] border border-[#E5E7EB] px-3 py-2 font-mono text-xs"
          />
        </div>
        <button
          onClick={propose}
          disabled={proposing || companyId === ""}
          className="mt-4 rounded-[10px] bg-[#2563EB] px-5 py-2.5 text-sm font-medium text-white hover:bg-[#2563EB]/90 disabled:opacity-50"
        >
          {proposing ? "Asking AI…" : "Propose Value Unlock Ideas"}
        </button>
      </Card>

      {Boolean(proposeError) && <ErrorState error={proposeError} context="POST /scenarios/value-unlock/propose (requires GEMINI_API_KEY)" />}

      {proposeResult && proposeResult.abstained && (
        <Card className="border-[#F59E0B]/40 bg-[#F59E0B]/[0.05]">
          <p className="text-sm text-[#B45309]">AI abstained: {proposeResult.reason}</p>
        </Card>
      )}

      {proposeResult && !proposeResult.abstained && (
        <AIPanel title="Proposed structural actions">
          <div className="space-y-3">
            {proposeResult.proposals.map((p) => {
              const decided = decisions[p.decision_id];
              return (
                <div key={p.decision_id} className="rounded-[8px] border border-[#E5E7EB] bg-white p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-sm font-semibold text-[#0B1F3A]">
                      {p.action_type} {p.target_segment ? `· ${p.target_segment}` : ""}
                    </p>
                    {decided ? (
                      <span className="text-xs font-semibold uppercase text-[#111827]/60">{decided.status}</span>
                    ) : (
                      <div className="flex gap-2">
                        <button onClick={() => decide(p.decision_id, "APPROVE")} className="rounded-[6px] border border-[#16A34A]/40 px-2 py-1 text-xs font-medium text-[#16A34A] hover:bg-[#16A34A]/5">
                          Approve
                        </button>
                        <button onClick={() => decide(p.decision_id, "REJECT")} className="rounded-[6px] border border-[#DC2626]/40 px-2 py-1 text-xs font-medium text-[#DC2626] hover:bg-[#DC2626]/5">
                          Reject
                        </button>
                      </div>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-[#111827]/60">{p.rationale}</p>
                  {decisionErrors[p.decision_id] ? <ErrorState error={decisionErrors[p.decision_id]} /> : null}
                  {decided?.status === "APPROVED" && (
                    <p className="mt-2 text-xs text-[#16A34A]">
                      Approved — compute via POST /scenarios/value-unlock/compute/{p.action_type} with decision_id={p.decision_id} and explicit params (multiples/proceeds/amounts).
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </AIPanel>
      )}
    </div>
  );
}
