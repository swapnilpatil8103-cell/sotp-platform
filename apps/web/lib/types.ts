/**
 * TypeScript types mirroring backend/schemas/* and the Pydantic response
 * models embedded in backend/valuation/* + backend/api/routers/*.
 *
 * Design choice (Phase 9): these are colocated in apps/web/lib/types.ts
 * rather than packages/shared-types, because the backend has no build step
 * that shares code with the frontend and the response shapes are Pydantic
 * models (snake_case) -- mirroring field names 1:1 here avoids a
 * transformation layer that would otherwise have to be kept in sync by hand
 * across ~10 schema files. Field names intentionally match the Python
 * schemas exactly (snake_case), not idiomatic TS camelCase, so that
 * `JSON.stringify`/`fetch().json()` results can be typed directly with zero
 * mapping code. packages/shared-types/index.ts (Phase 1 stub) is left as-is;
 * it can be reconciled with these once the backend types stabilize.
 */

export type DataStatus =
  | "REPORTED"
  | "DERIVED"
  | "ESTIMATED"
  | "MISSING"
  | "CONFLICTING";

// --- companies / filings / facts ------------------------------------------

export interface CompanyRead {
  ticker: string;
  cik: string;
  name: string;
  sic?: string | null;
  sic_description?: string | null;
  exchange?: string | null;
  fiscal_year_end?: string | null;
  company_id?: number | null;
}

export interface CompanyIdRead {
  ticker: string;
  company_id: number;
}

// --- memo -------------------------------------------------------------------

export interface MemoSectionResult {
  ai_origin: boolean;
  section: string;
  abstained: boolean;
  memo_text?: string | null;
  validation_ok?: boolean | null;
  validation_notes?: string | null;
  reason?: string | null;
}

export interface MemoResponse {
  ticker: string;
  company_id: number;
  company_name: string;
  insufficient_data: boolean;
  reason?: string | null;
  data_snapshot: Record<string, unknown>;
  sections: MemoSectionResult[];
}

export interface FilingRead {
  form: string;
  accession_number: string;
  filing_date?: string | null;
  period_of_report?: string | null;
  source_url: string;
}

export interface FilingsResponse {
  ticker: string;
  cik: string;
  filings: FilingRead[];
}

export interface FinancialFactRead {
  concept: string;
  value?: number | null;
  unit: string;
  currency: string;
  period: string;
  fiscal_year: number;
  fiscal_period: string;
  filing_date?: string | null;
  source: string;
  source_url?: string | null;
  accession_number?: string | null;
  xbrl_tag?: string | null;
  data_status: DataStatus;
}

export interface CompanyFactsRead {
  ticker: string;
  cik: string;
  fiscal_year: number;
  fiscal_period: string;
  facts: FinancialFactRead[];
}

export interface DividendRead {
  dividend_yield?: number | null;
  last_dividend_value?: number | null;
  last_dividend_date?: string | null;
}

export interface MarketDataRead {
  ticker: string;
  price?: number | null;
  currency?: string | null;
  market_cap?: number | null;
  shares_outstanding?: number | null;
  beta?: number | null;
  dividend: DividendRead;
  as_of?: string | null;
  source: string;
  fetched_at: string;
  is_latest_available_not_realtime: boolean;
}

// --- segments ---------------------------------------------------------

export interface SegmentFactRead {
  concept: string;
  value?: number | null;
  unit: string;
  currency: string;
  period: string;
  fiscal_year: number;
  fiscal_period: string;
  source: string;
  source_url?: string | null;
  accession_number?: string | null;
  xbrl_tag?: string | null;
  data_status: DataStatus;
}

export interface MetricCoverageRead {
  metric: string;
  reported_periods: number;
  total_periods: number;
  coverage_pct: number;
}

export interface SegmentRead {
  name: string;
  facts: SegmentFactRead[];
  coverage: MetricCoverageRead[];
  overall_coverage_pct: number;
}

export interface CompanySegmentsRead {
  ticker: string;
  cik: string;
  fiscal_year: number;
  fiscal_period: string;
  segments: SegmentRead[];
  overall_coverage_pct: number;
  note?: string | null;
}

// --- historical trends ---------------------------------------------------

export interface YearValueRead {
  fiscal_year: number;
  value?: number | null;
  data_status: DataStatus | string;
}

export interface CagrRead {
  concept: string;
  insufficient_history: boolean;
  reason?: string | null;
  start_year?: number | null;
  end_year?: number | null;
  start_value?: number | null;
  end_value?: number | null;
  num_years?: number | null;
  cagr_pct?: number | null;
  data_status: string;
}

export interface MarginTrendRead {
  concept: string;
  insufficient_history: boolean;
  reason?: string | null;
  years: YearValueRead[];
  min_margin_pct?: number | null;
  max_margin_pct?: number | null;
  latest_margin_pct?: number | null;
  average_margin_pct?: number | null;
  data_status: string;
}

export interface ForwardSuggestionRead {
  concept: string;
  insufficient_history: boolean;
  reason?: string | null;
  basis?: string | null;
  suggested_annual_growth_pct?: number | null;
  suggested_years?: number[] | null;
  suggested_values?: number[] | null;
  label: string;
}

export interface HistoricalTrendsRead {
  ticker: string;
  cik: string;
  company_id: number;
  fiscal_period: string;
  fiscal_years_covered: number[];
  series: Record<string, YearValueRead[]>;
  revenue_cagr: CagrRead;
  net_income_cagr: CagrRead;
  operating_margin_trend: MarginTrendRead;
  suggested_forward_revenue: ForwardSuggestionRead;
}

// --- valuation: DCF -----------------------------------------------------

export interface DcfInput {
  base_revenue: number;
  revenue_growth_rates: number[];
  ebit_margins: number[];
  tax_rate: number;
  da_pct_of_revenue: number[];
  capex_pct_of_revenue: number[];
  nwc_change_pct_of_revenue: number[];
  wacc: number;
  terminal_growth_rate: number;
  net_debt: number;
  cash_and_equivalents: number;
  investments?: number;
  minority_interest?: number;
  diluted_shares_outstanding: number;
  exit_multiple?: number | null;
}

export interface DcfYearProjection {
  year_index: number;
  revenue: number;
  ebit: number;
  nopat: number;
  da: number;
  capex: number;
  nwc_change: number;
  fcff: number;
  discount_factor: number;
  discounted_fcff: number;
}

export interface DcfResult {
  inputs: DcfInput;
  projections: DcfYearProjection[];
  terminal_value_undiscounted: number;
  terminal_value_discounted: number;
  enterprise_value: number;
  equity_value: number;
  implied_price_per_share: number;
  terminal_year_ebitda?: number | null;
  exit_multiple_terminal_value_undiscounted?: number | null;
  exit_multiple_terminal_value_discounted?: number | null;
  exit_multiple_enterprise_value?: number | null;
  exit_multiple_equity_value?: number | null;
  exit_multiple_implied_price_per_share?: number | null;
}

// --- valuation: comps -----------------------------------------------------

export interface CompPeer {
  name: string;
  revenue: number;
  ebitda: number;
  ebit: number;
  net_income: number;
  market_cap: number;
  debt: number;
  cash: number;
  book_value_equity?: number | null;
}

export type MultipleMetric =
  | "ev_to_revenue"
  | "ev_to_ebitda"
  | "ev_to_ebit"
  | "price_to_earnings"
  | "price_to_book";

export interface PeerMultiples {
  name: string;
  ev_to_revenue?: number | null;
  ev_to_ebitda?: number | null;
  ev_to_ebit?: number | null;
  price_to_earnings?: number | null;
  price_to_book?: number | null;
}

export interface MultipleStats {
  metric: MultipleMetric;
  min: number;
  p25: number;
  median: number;
  p75: number;
  max: number;
}

export interface CompsInput {
  peers: CompPeer[];
  target_name: string;
  target_revenue: number;
  target_ebitda: number;
  target_ebit: number;
  target_net_income: number;
  target_book_value_equity?: number | null;
  target_debt: number;
  target_cash: number;
  target_diluted_shares_outstanding: number;
  chosen_multiple_metric: MultipleMetric;
  chosen_multiple_value: number;
}

export interface CompsResult {
  inputs: CompsInput;
  peer_multiples: PeerMultiples[];
  stats: MultipleStats[];
  implied_enterprise_value?: number | null;
  implied_equity_value: number;
  implied_price_per_share: number;
}

// --- valuation: SOTP -----------------------------------------------------

export interface SotpSegmentInput {
  name: string;
  enterprise_value: number;
  ownership_pct: number;
}

export interface SotpInput {
  segments: SotpSegmentInput[];
  cash_and_equivalents: number;
  marketable_securities?: number;
  other_investments?: number;
  total_debt: number;
  minority_interest?: number;
  corporate_liabilities?: number;
  corporate_overhead_annual: number;
  overhead_capitalization_multiple: number;
  corporate_overhead_treatment: "direct_deduction" | "capitalized_overhead";
  diluted_shares_outstanding: number;
  current_share_price?: number | null;
}

export interface SotpResult {
  inputs: SotpInput;
  segment_attributed_evs: Record<string, number>;
  sum_of_segment_evs: number;
  non_operating_assets: number;
  capitalized_overhead_deduction: number;
  equity_value_direct_deduction: number;
  equity_value_capitalized_overhead: number;
  equity_value: number;
  implied_price_per_share: number;
  conglomerate_discount_pct?: number | null;
  upside_downside_pct?: number | null;
}

// --- sensitivity -----------------------------------------------------

export interface SensitivityInput {
  row_label: string;
  row_values: number[];
  col_label: string;
  col_values: number[];
}

export interface SensitivityResult {
  row_label: string;
  row_values: number[];
  col_label: string;
  col_values: number[];
  matrix: number[][];
}

// --- risk dashboard -----------------------------------------------------

export type RiskLevel = "LOW" | "MEDIUM" | "HIGH";

export interface RiskCategoryScore {
  category: string;
  score: RiskLevel;
  explanation: string;
  ai_assisted: boolean;
}

export interface RiskDashboardResult {
  data_risk: RiskCategoryScore;
  model_risk: RiskCategoryScore;
  forecast_risk: RiskCategoryScore;
  market_risk: RiskCategoryScore;
  strategic_risk: RiskCategoryScore;
  execution_risk: RiskCategoryScore;
}

// --- scenarios -----------------------------------------------------

export interface ScenarioRunInput {
  name: string;
  dcf_input: DcfInput;
}

export interface ScenarioOutcome {
  name: string;
  dcf_result: DcfResult;
  enterprise_value: number;
  equity_value: number;
  implied_price_per_share: number;
  upside_downside_pct?: number | null;
}

export interface ScenarioEngineInput {
  bull: ScenarioRunInput;
  base: ScenarioRunInput;
  bear: ScenarioRunInput;
  current_market_price?: number | null;
}

export interface ScenarioEngineResult {
  bull: ScenarioOutcome;
  base: ScenarioOutcome;
  bear: ScenarioOutcome;
  current_market_price?: number | null;
  ordering_valid: boolean;
}

// --- reverse valuation -----------------------------------------------------

export type ReverseValuationClassification =
  | "CONSERVATIVE"
  | "REASONABLE"
  | "AGGRESSIVE"
  | "EXTREME";

export interface RangeInput {
  low: number;
  high: number;
}

export interface ReverseValuationInput {
  base_dcf_input: DcfInput;
  target_enterprise_value: number;
  solve_target: "revenue_growth" | "terminal_growth";
  search_low: number;
  search_high: number;
  historical_range: RangeInput;
  peer_range: RangeInput;
}

export interface ReverseValuationResult {
  solve_target: "revenue_growth" | "terminal_growth";
  implied_value: number;
  target_enterprise_value: number;
  achieved_enterprise_value: number;
  historical_range: RangeInput;
  peer_range: RangeInput;
  position: number;
  classification: ReverseValuationClassification;
}

// --- value unlock -----------------------------------------------------

export type ValueUnlockActionType =
  | "spin_off"
  | "subsidiary_ipo"
  | "asset_sale"
  | "buyback"
  | "debt_reduction"
  | "special_dividend"
  | "segment_separation";

export interface ValueUnlockResult {
  action_type: ValueUnlockActionType;
  current_value: number;
  potential_value: number;
  estimated_uplift: number;
  uplift_pct?: number | null;
  detail: Record<string, unknown>;
}

export interface ValueUnlockProposal {
  decision_id: number;
  action_type: string;
  target_segment?: string | null;
  rationale: string;
}

export interface ValueUnlockProposeResponse {
  abstained: boolean;
  reason?: string | null;
  proposals: ValueUnlockProposal[];
}

// --- governance -----------------------------------------------------

export type AssumptionStatus = "PENDING" | "APPROVED" | "OVERRIDDEN" | "REJECTED";

export interface AssumptionDecisionOut {
  id: number;
  valuation_run_id?: number | null;
  company_id?: number | null;
  assumption_key: string;
  subject?: string | null;
  ai_recommended_value?: number | null;
  ai_rationale?: string | null;
  ai_confidence?: number | null;
  approved_value?: number | null;
  approval_reason?: string | null;
  status: AssumptionStatus;
  decided_by?: string | null;
  decided_at?: string | null;
  created_at: string;
}

export interface ValuationRunOut {
  id: number;
  company_id: number;
  method: string;
  version: number;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  created_at: string;
  created_by?: string | null;
}

export interface DiffResponse {
  company_id: number;
  from_version: number;
  to_version: number;
  method_from: string;
  method_to: string;
  changed_fields: Record<string, unknown>[];
  unchanged_field_count: number;
  key_changes: Record<string, unknown>[];
  summary: string;
}

// --- segment auto-valuation (SUGGESTED, pre-fill only) ---------------------

export interface SegmentFactsInput {
  name: string;
  revenue?: number | null;
  revenue_status?: string;
  ebit?: number | null;
  ebit_status?: string;
}

export interface SegmentMultipleAssumptionInput {
  metric: "ev_to_revenue" | "ev_to_ebit";
  multiple_value: number;
}

export interface SegmentDcfAssumptionInput {
  revenue_growth_rates: number[];
  ebit_margins: number[];
  tax_rate: number;
  da_pct_of_revenue: number[];
  capex_pct_of_revenue: number[];
  nwc_change_pct_of_revenue: number[];
  wacc: number;
  terminal_growth_rate: number;
  exit_multiple?: number | null;
}

export interface SegmentValuationRequestInput {
  segment: SegmentFactsInput;
  methodology: "multiple" | "dcf";
  multiple_assumption?: SegmentMultipleAssumptionInput | null;
  dcf_assumption?: SegmentDcfAssumptionInput | null;
}

export interface AutoValueSegmentsRequest {
  segments: SegmentValuationRequestInput[];
}

export interface SegmentValuationSuggestion {
  segment_name: string;
  methodology: "multiple" | "dcf";
  status: "SUGGESTED" | "ERROR";
  error?: string | null;
  suggested_enterprise_value?: number | null;
  dcf_result?: DcfResult | null;
  data_status: string;
  requires_review: boolean;
}

export interface AutoValueSegmentsResponse {
  suggestions: SegmentValuationSuggestion[];
  governance_note: string;
}

// --- peer-informed beta (SUGGESTED, pre-fill only) --------------------------

export interface PeerBetaInputRow {
  name: string;
  levered_beta: number;
  debt_to_equity?: number | null;
  tax_rate?: number | null;
}

export interface BetaAnalysisInput {
  peers: PeerBetaInputRow[];
  aggregation: "median" | "average";
  target_debt_to_equity: number;
  target_tax_rate: number;
}

export interface PeerUnleveredBeta {
  name: string;
  levered_beta: number;
  debt_to_equity: number;
  tax_rate: number;
  unlevered_beta: number;
}

export interface SkippedPeer {
  name: string;
  reason: string;
}

export interface BetaAnalysisResult {
  used_peers: PeerUnleveredBeta[];
  skipped_peers: SkippedPeer[];
  aggregation: "median" | "average";
  unlevered_beta_aggregate: number;
  target_debt_to_equity: number;
  target_tax_rate: number;
  relevered_beta: number;
}

export interface AuditTrailEntryOut {
  id: number;
  entity_type: string;
  entity_id?: number | null;
  action: string;
  role: string;
  actor?: string | null;
  detail: Record<string, unknown>;
  created_at: string;
}
