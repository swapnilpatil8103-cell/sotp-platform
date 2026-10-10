/**
 * Thin fetch wrapper around the FastAPI backend (backend/api/routers/*).
 *
 * Every call returns the parsed JSON on success or throws `ApiError`, which
 * every page catches to render a graceful error state (never a blank crash).
 * `ApiError.status === 0` means the request never reached the server at all
 * (network failure / backend unreachable) so pages can render "Backend
 * unavailable" specifically for that case.
 */

import type {
  AutoValueSegmentsRequest,
  AutoValueSegmentsResponse,
  BetaAnalysisInput,
  BetaAnalysisResult,
  RiskFreeRateSuggestion,
  CompanyFactsRead,
  CompanyIdRead,
  CompanyRead,
  CompanySegmentsRead,
  CompsInput,
  CompsResult,
  DcfInput,
  DcfResult,
  DiffResponse,
  FilingsResponse,
  HistoricalTrendsRead,
  MarketDataRead,
  MemoResponse,
  RiskDashboardResult,
  ScenarioEngineInput,
  ScenarioEngineResult,
  SensitivityInput,
  SensitivityResult,
  SotpInput,
  SotpResult,
  ValuationRunOut,
} from "./types";

// All backend calls go through the Next.js proxy route (app/api/backend), which
// attaches the session token from an httpOnly cookie. No credential is ever
// available to, or sent from, browser JavaScript. The proxy's own upstream
// address is the server-side API_BASE_URL env var.
const BASE_URL = "/api/backend";

/** Send the user to the login page (the session cookie is missing, expired or revoked). */
function redirectToLogin(): void {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search;
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

export class ApiError extends Error {
  status: number;
  detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch (err) {
    throw new ApiError(
      0,
      "Backend unavailable — could not reach the SOTP Intelligence API.",
      err instanceof Error ? err.message : String(err)
    );
  }

  if (res.status === 401) redirectToLogin();
  if (res.status === 502) {
    // The proxy could not reach the backend.
    throw new ApiError(0, "Backend unavailable — could not reach the SOTP Intelligence API.", undefined);
  }

  if (!res.ok) {
    let detail: string | undefined;
    try {
      const body = await res.json();
      detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail || `Request failed (${res.status})`, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

// --- companies / filings / facts / market data -----------------------------

export const getCompany = (ticker: string) =>
  request<CompanyRead>(`/companies/${encodeURIComponent(ticker)}`);

export const getCompanyId = (ticker: string) =>
  request<CompanyIdRead>(`/companies/${encodeURIComponent(ticker)}/id`);

export const getCompanyFilings = (ticker: string) =>
  request<FilingsResponse>(`/companies/${encodeURIComponent(ticker)}/filings`);

export const getCompanyFacts = (ticker: string, fiscalYear?: number) =>
  request<CompanyFactsRead>(
    `/companies/${encodeURIComponent(ticker)}/facts${qs({ fiscal_year: fiscalYear })}`
  );

export const getCompanySegments = (ticker: string, fiscalYear?: number) =>
  request<CompanySegmentsRead>(
    `/companies/${encodeURIComponent(ticker)}/segments${qs({ fiscal_year: fiscalYear })}`
  );

export const getHistoricalTrends = (
  ticker: string,
  opts?: { fiscalPeriod?: string; numYears?: number; numForecastYears?: number }
) =>
  request<HistoricalTrendsRead>(
    `/companies/${encodeURIComponent(ticker)}/historical-trends${qs({
      fiscal_period: opts?.fiscalPeriod,
      num_years: opts?.numYears,
      num_forecast_years: opts?.numForecastYears,
    })}`
  );

export const getMarketData = (ticker: string) =>
  request<MarketDataRead>(`/companies/${encodeURIComponent(ticker)}/market-data`);

// --- valuation engine (pure compute, caller-assembled inputs) --------------

export const computeSotp = (payload: SotpInput) =>
  request<SotpResult>(`/valuation/sotp`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const computeDcf = (payload: DcfInput) =>
  request<DcfResult>(`/valuation/dcf`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const computeComps = (payload: CompsInput) =>
  request<CompsResult>(`/valuation/comps`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const runScenarios = (payload: ScenarioEngineInput) =>
  request<ScenarioEngineResult>(`/scenarios/run`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const computeSensitivity = (
  kind: "dcf" | "comps",
  payload: SensitivityInput
) =>
  request<SensitivityResult>(`/valuation/sensitivity/${kind}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const autoValueSegments = (ticker: string, payload: AutoValueSegmentsRequest) =>
  request<AutoValueSegmentsResponse>(
    `/valuation/${encodeURIComponent(ticker)}/segments/auto-value`,
    { method: "POST", body: JSON.stringify(payload) }
  );

export const computePeerInformedBeta = (payload: BetaAnalysisInput) =>
  request<BetaAnalysisResult>(`/valuation/wacc/peer-beta`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const getSuggestedRiskFreeRate = (symbol: string = "US10Y") =>
  request<RiskFreeRateSuggestion>(
    `/valuation/wacc/risk-free-rate-suggestion?symbol=${encodeURIComponent(symbol)}`
  );

// --- governance / research history -----------------------------------------

export const listCompanyRuns = (companyId: number, method?: string) =>
  request<ValuationRunOut[]>(
    `/valuation/company/${companyId}/runs${qs({ method })}`
  );

export const diffCompanyRuns = (
  companyId: number,
  fromVersion: number,
  toVersion: number,
  method?: string
) =>
  request<DiffResponse>(
    `/valuation/company/${companyId}/runs/diff${qs({
      from_version: fromVersion,
      to_version: toVersion,
      method,
    })}`
  );

// --- memo --------------------------------------------------------------------

export const getMemo = (ticker: string) =>
  request<MemoResponse>(`/memo/${encodeURIComponent(ticker)}`);

export { BASE_URL as API_BASE_URL };
