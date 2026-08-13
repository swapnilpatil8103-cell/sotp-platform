"use client";

import { useEffect, useState } from "react";
import { ApiError, getCompanyId } from "@/lib/api";
import type { AssumptionDecisionOut, AuditTrailEntryOut } from "@/lib/types";
import { AIBadge, AIPanel, Card, ErrorState, SectionHeading } from "@/components/ui";

const BASE = () => process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "") ?? "http://localhost:8000";

/**
 * AI Analyst panel (NOT a chatbot): surfaces every AI-recommended
 * assumption for this company (GET /valuation/company/{id}/assumptions,
 * Phase 7 governance) with "Why? / View Evidence / Challenge / View
 * Sources" affordances, clearly badged as AI-origin content. "Why?" shows
 * the stored ai_rationale. "View Sources"/"View Evidence" pulls the
 * per-entity audit trail (GET /valuation/company/{id}/audit-trail).
 * "Challenge" records a human REJECT decision via the same governance
 * endpoint the Value Unlock page uses. company_id is resolved
 * automatically from the ticker via GET /companies/{ticker}/id.
 */
export default function AiAnalystPage({ params }: { params: { ticker: string } }) {
  const ticker = params.ticker.toUpperCase();
  const [companyId, setCompanyId] = useState<number | "">("");
  const [resolveError, setResolveError] = useState<unknown>(null);
  const [decisions, setDecisions] = useState<AssumptionDecisionOut[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, "why" | "evidence" | null>>({});
  const [evidence, setEvidence] = useState<Record<number, AuditTrailEntryOut[]>>({});

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
      load();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [companyId]);

  async function load() {
    if (companyId === "") return;
    setLoading(true);
    setError(null);
    setDecisions(null);
    try {
      const res = await fetch(`${BASE()}/valuation/company/${companyId}/assumptions`);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Failed to load assumptions", body.detail);
      }
      setDecisions(await res.json());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  async function toggle(id: number, mode: "why" | "evidence") {
    const isOpen = expanded[id] === mode;
    setExpanded((prev) => ({ ...prev, [id]: isOpen ? null : mode }));
    if (mode === "evidence" && !isOpen && !evidence[id]) {
      try {
        const res = await fetch(`${BASE()}/valuation/company/${companyId}/audit-trail`);
        if (res.ok) {
          const all: AuditTrailEntryOut[] = await res.json();
          setEvidence((prev) => ({ ...prev, [id]: all.filter((e) => e.entity_id === id) }));
        }
      } catch {
        /* evidence is best-effort */
      }
    }
  }

  async function challenge(id: number) {
    try {
      await fetch(`${BASE()}/valuation/assumptions/${id}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: "REJECT", user_id: "analyst:web-ui", reason: "Challenged from AI Analyst panel" }),
      });
      load();
    } catch {
      /* surfaced implicitly via reload */
    }
  }

  return (
    <div className="space-y-6">
      <SectionHeading
        title="AI Analyst"
        description={`Every AI-recommended assumption for ${ticker}, with full provenance. This is a recommendation panel, not a chatbot — every number an AI proposes must be human-approved before it reaches a valuation run.`}
      />

      {Boolean(resolveError) && (
        <ErrorState error={resolveError} context="GET /companies/{ticker}/id — could not resolve company_id" />
      )}

      <Card title="Company">
        <p className="text-xs text-[#111827]/50">
          company_id{" "}
          <span className="font-semibold tabular-nums text-[#111827]/80">{companyId === "" ? "resolving…" : companyId}</span>{" "}
          for {ticker} {loading && "· loading recommendations…"}
        </p>
      </Card>

      {Boolean(error) && <ErrorState error={error} context="GET /valuation/company/{id}/assumptions" />}

      {decisions && decisions.length === 0 && (
        <Card>
          <p className="text-sm text-[#111827]/50">No AI-recommended assumptions recorded for this company yet.</p>
        </Card>
      )}

      {decisions && decisions.length > 0 && (
        <div className="space-y-4">
          {decisions.map((d) => (
            <AIPanel key={d.id} title={d.assumption_key}>
              <div className="flex items-center justify-between">
                <p className="text-sm text-[#0B1F3A]">
                  Recommended value:{" "}
                  <span className="font-semibold tabular-nums">{d.ai_recommended_value ?? "—"}</span>
                  {d.ai_confidence != null && (
                    <span className="ml-2 text-xs text-[#111827]/50">confidence {(d.ai_confidence * 100).toFixed(0)}%</span>
                  )}
                </p>
                <span className="text-xs font-semibold uppercase text-[#111827]/50">{d.status}</span>
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button onClick={() => toggle(d.id, "why")} className="rounded-[6px] border border-[#0B1F3A]/20 px-2.5 py-1 text-xs font-medium text-[#0B1F3A] hover:bg-[#0B1F3A]/5">
                  Why?
                </button>
                <button onClick={() => toggle(d.id, "evidence")} className="rounded-[6px] border border-[#0B1F3A]/20 px-2.5 py-1 text-xs font-medium text-[#0B1F3A] hover:bg-[#0B1F3A]/5">
                  View Evidence / Sources
                </button>
                {d.status === "PENDING" && (
                  <button onClick={() => challenge(d.id)} className="rounded-[6px] border border-[#DC2626]/40 px-2.5 py-1 text-xs font-medium text-[#DC2626] hover:bg-[#DC2626]/5">
                    Challenge
                  </button>
                )}
              </div>

              {expanded[d.id] === "why" && (
                <p className="mt-3 rounded-[8px] bg-white p-3 text-xs text-[#111827]/80">{d.ai_rationale ?? "No rationale recorded."}</p>
              )}

              {expanded[d.id] === "evidence" && (
                <div className="mt-3 space-y-1.5 rounded-[8px] bg-white p-3">
                  {(evidence[d.id] ?? []).length === 0 ? (
                    <p className="text-xs text-[#111827]/50">No audit trail entries found for this decision.</p>
                  ) : (
                    evidence[d.id].map((e) => (
                      <div key={e.id} className="flex items-center justify-between text-xs">
                        <span className="text-[#111827]/70">
                          [{e.role}] {e.action} — {e.actor ?? "unknown"}
                        </span>
                        <span className="text-[#111827]/40">{new Date(e.created_at).toLocaleString()}</span>
                      </div>
                    ))
                  )}
                </div>
              )}
            </AIPanel>
          ))}
        </div>
      )}
    </div>
  );
}
