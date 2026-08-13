/**
 * Shared TypeScript types for SOTP Intelligence.
 *
 * Phase 1 stub: mirrors the shape of backend/models/ at a high level so the
 * frontend can start typing API responses as routers go live in later
 * phases. Kept intentionally minimal -- expand alongside each backend
 * schema (backend/schemas/) as it's implemented.
 */

export type DataStatus =
  | "REPORTED"
  | "DERIVED"
  | "ESTIMATED"
  | "MISSING"
  | "CONFLICTING";

export type ValuationMethod = "DCF" | "COMPS" | "SOTP";

export type AssumptionStatus =
  | "PENDING"
  | "APPROVED"
  | "OVERRIDDEN"
  | "REJECTED";

export interface Company {
  id: number;
  ticker: string;
  cik: string;
  name: string;
  sector?: string | null;
  industry?: string | null;
  exchange?: string | null;
  currency: string;
}

export interface FinancialFact {
  id: number;
  companyId: number;
  concept: string;
  value: number | null;
  unit: string;
  currency: string;
  period: string;
  fiscalYear: number;
  fiscalPeriod: string;
  source: string;
  sourceUrl?: string | null;
  accessionNumber?: string | null;
  xbrlTag?: string | null;
  dataStatus: DataStatus;
}

// Placeholder -- expanded in Phase 5+ alongside backend/valuation.
export interface ValuationRun {
  id: number;
  companyId: number;
  method: ValuationMethod;
  version: number;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
}
