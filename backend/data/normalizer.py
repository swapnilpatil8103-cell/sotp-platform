"""Normalize raw SEC XBRL company facts into FinancialFact rows.

Given a company's raw XBRL "companyfacts" JSON and the concept mapping layer
(`backend.data.concept_mapping`), produce `FinancialFact` model instances for
a target fiscal year/period, with full provenance (source, source_url,
accession_number, xbrl_tag) and an honest `data_status`:

- REPORTED: a candidate XBRL tag had a value for the requested period.
- MISSING: none of the candidate tags had data for that period (value=None).

No estimation/derivation happens here — that's explicitly out of scope.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any, Optional

from backend.data.concept_mapping import CANONICAL_CONCEPTS, resolve_concept
from backend.models.enums import DataStatus
from backend.models.financial_fact import FinancialFact

SEC_SOURCE = "SEC XBRL"

# Concepts whose XBRL `end` date is NOT a fiscal-period end and so must be
# excluded from any scan that infers a fiscal year from `end` (see
# `_true_fiscal_year`). `shares_outstanding` (dei:EntityCommonStockSharesOutstanding)
# is a cover-page "as of" fact: its `end` is the 10-K/10-Q *filing* cover date
# (e.g. an FY2025 10-K filed in Feb 2026 reports shares outstanding "as of
# 2026-01-31" with fp="FY" but end=2026-01-31), which would otherwise get
# misread as fiscal year 2026. Confirmed against real JPM XBRL data.
YEAR_SCAN_EXCLUDED_CONCEPTS = {"shares_outstanding"}


def _true_fiscal_year(fact: dict[str, Any]) -> Optional[int]:
    """Derive the ACTUAL fiscal year a fact's period covers, from its own `end` date.

    Do not use this on facts you haven't already confirmed match the requested
    `fp`/period length -- this only extracts a calendar year from `end`.
    """
    end = _parse_date(fact.get("end"))
    if end is None:
        return None
    return end.year


def _pick_fact_for_period(
    unit_facts: list[dict[str, Any]],
    fiscal_year: int,
    fiscal_period: str,
) -> tuple[Optional[dict[str, Any]], bool]:
    """Pick the XBRL fact entry matching the requested fiscal year + period (e.g. FY/Q1..Q4).

    Returns ``(fact_entry, is_conflicting)``. ``fact_entry`` is ``None`` when
    nothing matches the period. ``is_conflicting`` is True when
    `concept_mapping.resolve_concept` found two different candidate XBRL tags
    genuinely disagreeing on the value for this exact period (see its
    docstring) -- callers should surface `DataStatus.CONFLICTING` rather than
    reporting the chosen value as a plain REPORTED fact. Each `unit_facts`
    entry (post-merge) also carries its own `_xbrl_tag` -- use that for
    per-value provenance instead of a single company-wide tag, since the
    merged list can now span more than one literal XBRL tag.

    XBRL "units" entries look like:
    {"end": "2023-09-30", "val": 123, "fy": 2023, "fp": "FY", "form": "10-K",
     "filed": "2023-11-03", "start": "2022-10-01", "accn": "0000320193-23-000106"}

    IMPORTANT / BUG HISTORY: this used to match on SEC's raw `fy` field
    (`f.get("fy") == fiscal_year`). That field is NOT the calendar fiscal year
    the period covers -- it's metadata about which annual filing's XBRL
    submission the datapoint was tagged under (a given period commonly shows
    up tagged with several different `fy` values because it's re-reported as
    a prior-year comparative column in later 10-Ks). E.g. real GOOGL XBRL data
    has the period 2020-01-01/2020-12-31 tagged `fy=2022` (because it appears
    as a comparative column in the FY2022 10-K), so matching on raw `fy` would
    silently return actual-FY2020 revenue when asked for FY2022 -- shifting an
    entire historical series by ~2 years. This is a real, confirmed SEC XBRL
    quirk, not a hypothetical.
    #
    # The correct, standard convention (also how companies with a non-calendar
    # fiscal year-end label their own fiscal years, e.g. Apple's FY2024 ended
    # September 2024) is: the fiscal year of an annual/instant fact is the
    # calendar year in which the period's `end` date falls. We derive that
    # from `end` ourselves instead of trusting `fy`. `fp` is still used to
    # select the right period *type* (FY vs Q1..Q4), and for annual facts we
    # additionally sanity-check the duration is ~annual (not a stray quarterly
    # entry that happens to share fp="FY" metadata) so we don't conflate
    # annual and quarterly windows.
    """
    fp_matches = [f for f in unit_facts if f.get("fp") == fiscal_period]

    candidates = []
    for f in fp_matches:
        end = _parse_date(f.get("end"))
        if end is None or end.year != fiscal_year:
            continue
        if fiscal_period == "FY":
            start = _parse_date(f.get("start"))
            if start is not None:
                duration_days = (end - start).days
                # Annual periods run ~350-380 days; reject anything shorter
                # (e.g. a stray quarterly-length entry mislabeled fp="FY").
                if duration_days < 300:
                    continue
        else:
            # Quarterly: reject anything that looks like an annual-length
            # duration accidentally carrying a quarterly fp.
            start = _parse_date(f.get("start"))
            if start is not None:
                duration_days = (end - start).days
                if duration_days > 130:
                    continue
        candidates.append(f)

    if not candidates:
        return None, False
    # Stable multi-key sort (least-significant key sorted first): prefer the
    # most recently filed value in case of duplicates/restatements *within* a
    # tag, then -- as the dominant key -- prefer the higher-priority
    # candidate XBRL tag (lower `_tag_priority` == earlier/more-specific
    # candidate, per concept_mapping.resolve_concept's merge order).
    candidates.sort(key=lambda f: f.get("filed", ""), reverse=True)
    candidates.sort(key=lambda f: f.get("_tag_priority", 0))
    chosen = candidates[0]
    is_conflicting = bool(chosen.get("_conflicting"))
    return chosen, is_conflicting


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_company_facts(
    company_id: int,
    company_facts: dict[str, Any],
    fiscal_year: int,
    fiscal_period: str = "FY",
    filing_id: Optional[int] = None,
    cik10: Optional[str] = None,
) -> list[FinancialFact]:
    """Produce one FinancialFact per canonical concept for a given fiscal year/period.

    Parameters
    ----------
    company_id: FK to the Company row these facts belong to.
    company_facts: raw XBRL companyfacts JSON from SECConnector.get_company_facts().
    fiscal_year: target fiscal year (e.g. 2023).
    fiscal_period: target fiscal period code as used by SEC XBRL ("FY", "Q1".."Q4").
    filing_id: optional FK to the specific Filing row, if known.
    cik10: zero-padded CIK, used to build a source_url when possible.
    """
    results: list[FinancialFact] = []

    for concept in CANONICAL_CONCEPTS:
        match = resolve_concept(concept, company_facts)

        if not match.matched or not match.facts:
            results.append(
                FinancialFact(
                    company_id=company_id,
                    filing_id=filing_id,
                    concept=concept,
                    value=None,
                    unit="USD",
                    currency="USD",
                    period=f"{fiscal_year}-{fiscal_period}",
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    filing_date=None,
                    source=SEC_SOURCE,
                    source_url=None,
                    accession_number=None,
                    xbrl_tag=None,
                    data_status=DataStatus.MISSING,
                )
            )
            continue

        fact_entry, is_conflicting = _pick_fact_for_period(match.facts, fiscal_year, fiscal_period)

        if fact_entry is None:
            results.append(
                FinancialFact(
                    company_id=company_id,
                    filing_id=filing_id,
                    concept=concept,
                    value=None,
                    unit=match.unit or "USD",
                    currency="USD",
                    period=f"{fiscal_year}-{fiscal_period}",
                    fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period,
                    filing_date=None,
                    source=SEC_SOURCE,
                    source_url=None,
                    accession_number=None,
                    xbrl_tag=match.tag,
                    data_status=DataStatus.MISSING,
                )
            )
            continue

        accession = fact_entry.get("accn")
        source_url = None
        if accession and cik10:
            accession_nodash = accession.replace("-", "")
            source_url = f"https://www.sec.gov/cgi-bin/viewer?action=view&cik={int(cik10)}&accession_number={accession}"
            source_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accession_nodash}/"

        results.append(
            FinancialFact(
                company_id=company_id,
                filing_id=filing_id,
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


def latest_available_fiscal_year(company_facts: dict[str, Any], fiscal_period: str = "FY") -> Optional[int]:
    """Best-effort scan across all concepts to find the most recent fiscal year with any data.

    Derives fiscal year from each fact's own `end` date, NOT SEC's raw `fy`
    metadata field -- see the comment on `_pick_fact_for_period` for why `fy`
    cannot be trusted for this.
    """
    years: set[int] = set()
    for concept in CANONICAL_CONCEPTS:
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
    if not years:
        return None
    return max(years)
