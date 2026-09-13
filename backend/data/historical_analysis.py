"""Deterministic historical trend/forecast-suggestion module.

No AI, no interpolation, no fabricated data points. Everything here operates
on REPORTED (or MISSING) `FinancialFact` rows already produced by
`backend.data.normalizer`, extended here to cover *multiple* fiscal years at
once instead of the single latest year the `/companies/{ticker}/facts`
endpoint has historically returned.

Two responsibilities:

1. `normalize_company_facts_multi_year` -- extract N years of normalized
   `FinancialFact` rows per canonical concept from a company's already-fetched
   XBRL `companyfacts` payload (`SECConnector.get_company_facts`). This does
   not perform any new SEC calls; the raw multi-year history is already
   present in that payload, this just surfaces more of it than
   `normalize_company_facts` (which only extracts a single fiscal year).

2. `compute_historical_trends` -- given that multi-year series, compute:
   - revenue CAGR over the available REPORTED history
   - operating margin trend (min/max/latest/average) over REPORTED years
   - simple forward growth/margin suggestions for the next N years, clearly
     tagged as SUGGESTED (derived from history, not a forecast the system
     has any confidence is realized)

If fewer than 2 REPORTED years of a concept are available, the corresponding
trend/suggestion comes back with `insufficient_history=True` and no
fabricated numbers -- never a trend drawn from a single point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from backend.data.concept_mapping import CANONICAL_CONCEPTS, resolve_concept
from backend.data.normalizer import (
    SEC_SOURCE,
    YEAR_SCAN_EXCLUDED_CONCEPTS,
    _parse_date,
    _pick_fact_for_period,
    _true_fiscal_year,
)
from backend.models.enums import DataStatus
from backend.models.financial_fact import FinancialFact

# Concepts this module computes trends for. Kept narrow and specific to what
# a Goldman-style history/forecast review actually asks for.
TREND_CONCEPTS = ("revenue", "operating_income", "net_income", "total_debt", "long_term_debt", "current_debt")


def normalize_company_facts_multi_year(
    company_id: int,
    company_facts: dict[str, Any],
    fiscal_years: list[int],
    fiscal_period: str = "FY",
    cik10: Optional[str] = None,
    concepts: Optional[list[str]] = None,
) -> list[FinancialFact]:
    """Produce FinancialFact rows for every (concept, fiscal_year) pair requested.

    Mirrors `normalizer.normalize_company_facts` exactly (same REPORTED/MISSING
    logic, same provenance fields) but loops over multiple fiscal years so the
    caller gets a real multi-year series instead of one point per call.
    """
    concepts = concepts or CANONICAL_CONCEPTS
    results: list[FinancialFact] = []

    for concept in concepts:
        match = resolve_concept(concept, company_facts)

        for fiscal_year in fiscal_years:
            if not match.matched or not match.facts:
                results.append(
                    _missing_fact(company_id, concept, fiscal_year, fiscal_period, unit="USD", xbrl_tag=None)
                )
                continue

            fact_entry, is_conflicting = _pick_fact_for_period(match.facts, fiscal_year, fiscal_period)
            if fact_entry is None:
                results.append(
                    _missing_fact(
                        company_id, concept, fiscal_year, fiscal_period, unit=match.unit or "USD", xbrl_tag=match.tag
                    )
                )
                continue

            accession = fact_entry.get("accn")
            source_url = None
            if accession and cik10:
                accession_nodash = accession.replace("-", "")
                source_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accession_nodash}/"

            results.append(
                FinancialFact(
                    company_id=company_id,
                    concept=concept,
                    value=fact_entry.get("val"),
                    unit=match.unit or "USD",
                    currency="USD",
                    period=f"{fiscal_year}-{fiscal_period}",
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    filing_date=_parse_date(fact_entry.get("filed")),
                    source=SEC_SOURCE,
                    source_url=source_url,
                    accession_number=accession,
                    xbrl_tag=fact_entry.get("_xbrl_tag", match.tag),
                    data_status=DataStatus.CONFLICTING if is_conflicting else DataStatus.REPORTED,
                )
            )

    return results


def _missing_fact(company_id, concept, fiscal_year, fiscal_period, unit, xbrl_tag) -> FinancialFact:
    return FinancialFact(
        company_id=company_id,
        concept=concept,
        value=None,
        unit=unit,
        currency="USD",
        period=f"{fiscal_year}-{fiscal_period}",
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        filing_date=None,
        source=SEC_SOURCE,
        source_url=None,
        accession_number=None,
        xbrl_tag=xbrl_tag,
        data_status=DataStatus.MISSING,
    )


def available_fiscal_years(
    company_facts: dict[str, Any],
    fiscal_period: str = "FY",
    concepts: Optional[list[str]] = None,
) -> list[int]:
    """Every fiscal year for which at least one of `concepts` has REPORTED data.

    Derives fiscal year from each fact's own `end` date, NOT SEC's raw `fy`
    metadata field -- see the comment on `normalizer._pick_fact_for_period`
    for why `fy` cannot be trusted for this.
    """
    concepts = concepts or CANONICAL_CONCEPTS
    years: set[int] = set()
    for concept in concepts:
        if concept in YEAR_SCAN_EXCLUDED_CONCEPTS:
            continue
        match = resolve_concept(concept, company_facts)
        if not match.matched or not match.facts:
            continue
        for f in match.facts:
            if f.get("fp") != fiscal_period:
                continue
            year = _true_fiscal_year(f)
            if year is not None:
                years.add(year)
    return sorted(years)


# --- trend computation -------------------------------------------------------


@dataclass
class YearValue:
    fiscal_year: int
    value: Optional[float]
    data_status: str


@dataclass
class CagrResult:
    concept: str
    insufficient_history: bool
    reason: Optional[str] = None
    start_year: Optional[int] = None
    end_year: Optional[int] = None
    start_value: Optional[float] = None
    end_value: Optional[float] = None
    num_years: Optional[int] = None
    cagr_pct: Optional[float] = None
    data_status: str = "DERIVED"


@dataclass
class MarginTrendResult:
    concept: str
    insufficient_history: bool
    reason: Optional[str] = None
    years: list[YearValue] = field(default_factory=list)
    min_margin_pct: Optional[float] = None
    max_margin_pct: Optional[float] = None
    latest_margin_pct: Optional[float] = None
    average_margin_pct: Optional[float] = None
    data_status: str = "DERIVED"


@dataclass
class ForwardSuggestion:
    concept: str
    insufficient_history: bool
    reason: Optional[str] = None
    basis: Optional[str] = None
    suggested_annual_growth_pct: Optional[float] = None
    suggested_years: Optional[list[int]] = None
    suggested_values: Optional[list[float]] = None
    label: str = "SUGGESTED"


@dataclass
class HistoricalTrendsResult:
    ticker: str
    concept: str
    reported_years: list[YearValue]
    cagr: CagrResult
    forward_suggestion: ForwardSuggestion


def _reported_year_values(facts: list[FinancialFact], concept: str) -> list[YearValue]:
    rows = [f for f in facts if f.concept == concept]
    rows.sort(key=lambda f: f.fiscal_year)
    return [YearValue(fiscal_year=f.fiscal_year, value=f.value, data_status=f.data_status.value) for f in rows]


def compute_cagr(years: list[YearValue], concept: str = "revenue") -> CagrResult:
    """CAGR over the earliest-to-latest REPORTED (non-null) values only.

    CAGR = (end_value / start_value) ** (1 / num_years) - 1, where num_years
    is the number of years spanned between the earliest and latest REPORTED
    points (not merely the count of points -- gaps are respected).

    Requires >= 2 REPORTED points spanning >= 1 year, and a positive
    start_value (CAGR is undefined/misleading for negative or zero bases).
    """
    reported = [y for y in years if y.data_status == "REPORTED" and y.value is not None]
    if len(reported) < 2:
        return CagrResult(
            concept=concept,
            insufficient_history=True,
            reason=f"Fewer than 2 REPORTED years of {concept} available ({len(reported)} found); cannot compute a CAGR.",
        )

    start = reported[0]
    end = reported[-1]
    num_years = end.fiscal_year - start.fiscal_year

    if num_years <= 0:
        return CagrResult(
            concept=concept,
            insufficient_history=True,
            reason="REPORTED years do not span a positive number of years.",
        )

    if start.value is None or start.value <= 0:
        return CagrResult(
            concept=concept,
            insufficient_history=True,
            reason="Earliest REPORTED value is zero or negative; CAGR is undefined.",
        )

    cagr = (end.value / start.value) ** (1.0 / num_years) - 1.0

    return CagrResult(
        concept=concept,
        insufficient_history=False,
        start_year=start.fiscal_year,
        end_year=end.fiscal_year,
        start_value=start.value,
        end_value=end.value,
        num_years=num_years,
        cagr_pct=cagr * 100.0,
    )


def compute_margin_trend(
    numerator_years: list[YearValue],
    denominator_years: list[YearValue],
    concept: str = "operating_margin",
) -> MarginTrendResult:
    """Margin = numerator / denominator per fiscal year, REPORTED-only on both sides."""
    denom_by_year = {y.fiscal_year: y for y in denominator_years}
    margins: list[YearValue] = []

    for num in numerator_years:
        denom = denom_by_year.get(num.fiscal_year)
        if (
            num.data_status != "REPORTED"
            or num.value is None
            or denom is None
            or denom.data_status != "REPORTED"
            or denom.value in (None, 0)
        ):
            continue
        margins.append(YearValue(fiscal_year=num.fiscal_year, value=(num.value / denom.value) * 100.0, data_status="DERIVED"))

    if len(margins) < 2:
        return MarginTrendResult(
            concept=concept,
            insufficient_history=True,
            reason=f"Fewer than 2 years with both REPORTED numerator and denominator for {concept} ({len(margins)} found).",
        )

    margins.sort(key=lambda m: m.fiscal_year)
    values = [m.value for m in margins]
    return MarginTrendResult(
        concept=concept,
        insufficient_history=False,
        years=margins,
        min_margin_pct=min(values),
        max_margin_pct=max(values),
        latest_margin_pct=values[-1],
        average_margin_pct=sum(values) / len(values),
    )


def suggest_forward_growth(cagr_result: CagrResult, num_forecast_years: int = 5) -> ForwardSuggestion:
    """Project `num_forecast_years` of forward values off the historical CAGR.

    This is a deterministic, clearly-labeled SUGGESTION -- a straight-line
    extrapolation of the company's own reported historical growth rate, not a
    prediction the system has confidence in. Callers/UI must not present it
    as a forecast on par with REPORTED data.
    """
    if cagr_result.insufficient_history or cagr_result.cagr_pct is None or cagr_result.end_value is None:
        return ForwardSuggestion(
            concept=cagr_result.concept,
            insufficient_history=True,
            reason=cagr_result.reason or "Insufficient history to suggest a forward growth rate.",
        )

    growth_rate = cagr_result.cagr_pct / 100.0
    values = []
    current = cagr_result.end_value
    years = []
    for i in range(1, num_forecast_years + 1):
        current = current * (1 + growth_rate)
        values.append(current)
        years.append(cagr_result.end_year + i)

    return ForwardSuggestion(
        concept=cagr_result.concept,
        insufficient_history=False,
        basis=(
            f"Straight-line extrapolation of the historical {cagr_result.cagr_pct:.2f}% CAGR "
            f"observed from FY{cagr_result.start_year} to FY{cagr_result.end_year} "
            f"({cagr_result.num_years} year(s) of REPORTED data)."
        ),
        suggested_annual_growth_pct=cagr_result.cagr_pct,
        suggested_years=years,
        suggested_values=values,
    )


def build_historical_trends(
    company_id: int,
    ticker: str,
    company_facts: dict[str, Any],
    fiscal_period: str = "FY",
    cik10: Optional[str] = None,
    num_years: int = 6,
    num_forecast_years: int = 5,
) -> dict[str, Any]:
    """Assemble the full response for GET /companies/{ticker}/historical-trends.

    Extracts up to `num_years` most recent fiscal years of REPORTED history
    (from the already-fetched `company_facts` payload) for TREND_CONCEPTS,
    then computes revenue CAGR, operating margin trend, and a SUGGESTED
    forward revenue projection.
    """
    all_years = available_fiscal_years(company_facts, fiscal_period, concepts=list(TREND_CONCEPTS))
    target_years = all_years[-num_years:] if all_years else []

    facts = normalize_company_facts_multi_year(
        company_id=company_id,
        company_facts=company_facts,
        fiscal_years=target_years,
        fiscal_period=fiscal_period,
        cik10=cik10,
        concepts=list(TREND_CONCEPTS),
    )

    series: dict[str, list[YearValue]] = {
        concept: _reported_year_values(facts, concept) for concept in TREND_CONCEPTS
    }

    revenue_years = series["revenue"]
    operating_income_years = series["operating_income"]

    revenue_cagr = compute_cagr(revenue_years, concept="revenue")
    net_income_cagr = compute_cagr(series["net_income"], concept="net_income")
    operating_margin_trend = compute_margin_trend(operating_income_years, revenue_years, concept="operating_margin")
    forward_revenue = suggest_forward_growth(revenue_cagr, num_forecast_years=num_forecast_years)

    return {
        "ticker": ticker.upper(),
        "fiscal_period": fiscal_period,
        "fiscal_years_covered": target_years,
        "series": {
            concept: [
                {"fiscal_year": y.fiscal_year, "value": y.value, "data_status": y.data_status} for y in years
            ]
            for concept, years in series.items()
        },
        "revenue_cagr": _cagr_to_dict(revenue_cagr),
        "net_income_cagr": _cagr_to_dict(net_income_cagr),
        "operating_margin_trend": _margin_trend_to_dict(operating_margin_trend),
        "suggested_forward_revenue": _forward_to_dict(forward_revenue),
        "facts": facts,
    }


def _cagr_to_dict(r: CagrResult) -> dict[str, Any]:
    return {
        "concept": r.concept,
        "insufficient_history": r.insufficient_history,
        "reason": r.reason,
        "start_year": r.start_year,
        "end_year": r.end_year,
        "start_value": r.start_value,
        "end_value": r.end_value,
        "num_years": r.num_years,
        "cagr_pct": r.cagr_pct,
        "data_status": r.data_status,
    }


def _margin_trend_to_dict(r: MarginTrendResult) -> dict[str, Any]:
    return {
        "concept": r.concept,
        "insufficient_history": r.insufficient_history,
        "reason": r.reason,
        "years": [{"fiscal_year": y.fiscal_year, "value": y.value, "data_status": y.data_status} for y in r.years],
        "min_margin_pct": r.min_margin_pct,
        "max_margin_pct": r.max_margin_pct,
        "latest_margin_pct": r.latest_margin_pct,
        "average_margin_pct": r.average_margin_pct,
        "data_status": r.data_status,
    }


def _forward_to_dict(r: ForwardSuggestion) -> dict[str, Any]:
    return {
        "concept": r.concept,
        "insufficient_history": r.insufficient_history,
        "reason": r.reason,
        "basis": r.basis,
        "suggested_annual_growth_pct": r.suggested_annual_growth_pct,
        "suggested_years": r.suggested_years,
        "suggested_values": r.suggested_values,
        "label": r.label,
    }
