"""Normalize raw SEC XBRL company facts into FinancialFact rows.

Given a company's raw XBRL "companyfacts" JSON and the concept mapping layer
(`backend.data.concept_mapping`), produce `FinancialFact` model instances for
a target fiscal year/period, with full provenance (source, source_url,
accession_number, xbrl_tag) and an honest `data_status`:

- REPORTED: a candidate XBRL tag had a value for the requested period.
- DERIVED: no single tag reports it, so it is the exact sum of REPORTED components
  (only ``total_debt``; the components are listed in ``status_note``).
- CONFLICTING: the same tag and period carry different values in different filings
  (a restatement/recast). The most recently filed value is returned, and
  ``status_note`` lists every value with the filing it came from.
- MISSING: none of the candidate tags had data for that period (value=None).

Nothing is estimated: a DERIVED value is arithmetic on facts that were reported, and
every status other than a plain REPORTED lookup carries a ``status_note`` saying why.

Both ingestion paths (``normalize_company_facts`` here and the multi-year path in
``backend.data.historical_analysis``) go through ``build_fact``, so they cannot drift
apart again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import Any, Optional

from backend.data.concept_mapping import (
    CANONICAL_CONCEPTS,
    REVENUE_CONTRACT_TAGS,
    REVENUE_TOTAL_TAG,
    SHARES_BALANCE_SHEET_TAG,
    SHARES_COVER_TAG,
    TOTAL_DEBT_CURRENT_PARTS,
    TOTAL_DEBT_CURRENT_TOTAL_TAG,
    TOTAL_DEBT_NONCURRENT_TAGS,
    resolve_concept,
    resolve_tags,
)
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

# Forms that carry a company's annual report.
ANNUAL_FORM_PREFIXES = ("10-K", "20-F", "40-F")
# Concepts probed (in order) to find the original annual filing for a fiscal year.
ANCHOR_CONCEPTS = ("revenue", "net_income", "operating_income", "pretax_income", "cash")


def _true_fiscal_year(fact: dict[str, Any]) -> Optional[int]:
    """Derive the ACTUAL fiscal year a fact's period covers, from its own `end` date.

    Do not use this on facts you haven't already confirmed match the requested
    `fp`/period length -- this only extracts a calendar year from `end`.
    """
    end = _parse_date(fact.get("end"))
    if end is None:
        return None
    return end.year


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Picking one fact for a period
# --------------------------------------------------------------------------


@dataclass
class Pick:
    """The outcome of choosing a fact for a period."""

    fact: Optional[dict[str, Any]]
    status: DataStatus = DataStatus.MISSING
    note: Optional[str] = None


def _period_candidates(unit_facts: list[dict[str, Any]], fiscal_year: int, fiscal_period: str) -> list[dict[str, Any]]:
    """Entries matching the requested fiscal year + period type (FY / Q1..Q4).

    The fiscal year of a fact is the calendar year in which its period's `end` date falls;
    SEC's raw `fy` field is NOT usable for this. `fy` is metadata about which filing a
    datapoint was tagged under: a period commonly shows up tagged with several `fy`
    values because it is re-reported as a prior-year comparative column in later 10-Ks.
    E.g. real GOOGL data has 2020-01-01/2020-12-31 tagged `fy=2022` (a comparative in the
    FY2022 10-K), so matching on `fy` returns FY2020 revenue when FY2022 is asked for,
    shifting a whole historical series by ~2 years. `fp` still selects the period type;
    annual facts must span ~a year and quarterly ones ~a quarter, so a stray quarterly
    entry carrying fp="FY" (e.g. Q4 in a 10-K note) is not conflated with the year.
    """
    candidates = []
    for f in unit_facts:
        if f.get("fp") != fiscal_period:
            continue
        end = _parse_date(f.get("end"))
        if end is None or end.year != fiscal_year:
            continue
        start = _parse_date(f.get("start"))
        if start is not None:
            duration_days = (end - start).days
            if fiscal_period == "FY":
                if duration_days < 300:  # annual periods run ~350-380 days
                    continue
            elif duration_days > 130:  # a quarterly fp must not carry an annual-length window
                continue
        candidates.append(f)
    return candidates


def _fmt(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:,.0f}" if abs(value) >= 1000 or float(value).is_integer() else f"{value:,.4f}"
    return str(value)


def _filing_label(f: dict[str, Any]) -> str:
    return f"{f.get('form') or 'filing'} filed {f.get('filed') or '?'} ({f.get('accn') or 'no accession'})"


def _restatement_note(chosen: dict[str, Any], group: list[dict[str, Any]]) -> str:
    """Explain a value that different filings report differently for the same tag and period."""
    ordered = sorted(group, key=lambda f: f.get("filed", ""), reverse=True)
    lines = [f"{_fmt(f.get('val'))} per {_filing_label(f)}" for f in ordered]
    later, earlier = ordered[0], ordered[-1]
    change = ""
    try:
        if earlier["val"]:
            change = f" ({(later['val'] - earlier['val']) / abs(earlier['val']) * 100:+.1f}% from the earliest)"
    except (KeyError, TypeError):
        pass
    return (
        f"Restated or recast: {chosen.get('_xbrl_tag')} for {chosen.get('start') or 'as of'}..{chosen.get('end')} "
        f"has {len(ordered)} different values{change}: " + "; ".join(lines)
        + ". The most recently filed value is returned; check the filings before relying on a multi-year comparison."
    )


def _prefer_total_revenue(candidates: list[dict[str, Any]], chosen: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
    """If the chosen revenue tag is a contract-revenue subset and the same filing reports a larger
    total `Revenues` for the same period, use the total (and say so)."""
    if chosen.get("_xbrl_tag") not in REVENUE_CONTRACT_TAGS:
        return chosen, None
    totals = [
        c
        for c in candidates
        if c.get("_xbrl_tag") == REVENUE_TOTAL_TAG
        and (c.get("start"), c.get("end"), c.get("accn")) == (chosen.get("start"), chosen.get("end"), chosen.get("accn"))
        and c.get("val") is not None
    ]
    if not totals:
        return chosen, None
    total = max(totals, key=lambda c: c.get("filed", ""))
    if total["val"] > chosen["val"]:
        return total, (
            f"Total revenues ({REVENUE_TOTAL_TAG} {_fmt(total['val'])}) used: the revenue-from-contracts-with-customers "
            f"tag ({_fmt(chosen['val'])}) in the same filing is a subset of it."
        )
    return chosen, None


def pick_fact(
    unit_facts: list[dict[str, Any]],
    fiscal_year: int,
    fiscal_period: str,
    concept: Optional[str] = None,
) -> Pick:
    """Choose the XBRL fact for the requested fiscal year + period.

    Order of preference: the highest-priority candidate tag (`_tag_priority`), and within
    it the most recently filed entry. For ``concept == "revenue"`` a larger total
    `Revenues` supersedes a contract-revenue subset reported in the same filing.

    If the chosen tag has different values for this exact period in different filings
    (a restatement), the result is CONFLICTING with a note listing each value and its
    filing; the value is the most recently filed one.
    """
    candidates = _period_candidates(unit_facts, fiscal_year, fiscal_period)
    if not candidates:
        return Pick(None, DataStatus.MISSING)

    # Stable multi-key sort: most recently filed first, then (dominant) the higher-priority tag.
    candidates.sort(key=lambda f: f.get("filed", ""), reverse=True)
    candidates.sort(key=lambda f: f.get("_tag_priority", 0))
    chosen = candidates[0]

    note: Optional[str] = None
    if concept == "revenue":
        chosen, note = _prefer_total_revenue(candidates, chosen)

    group = [
        c
        for c in candidates
        if c.get("_xbrl_tag") == chosen.get("_xbrl_tag") and (c.get("start"), c.get("end")) == (chosen.get("start"), chosen.get("end"))
    ]
    if len({g.get("val") for g in group}) > 1:
        newest = max(group, key=lambda f: f.get("filed", ""))
        return Pick(newest, DataStatus.CONFLICTING, _restatement_note(newest, group))
    return Pick(chosen, DataStatus.REPORTED, note)


def _pick_fact_for_period(
    unit_facts: list[dict[str, Any]],
    fiscal_year: int,
    fiscal_period: str,
) -> tuple[Optional[dict[str, Any]], bool]:
    """Backward-compatible wrapper: ``(fact_entry, is_conflicting)``. See ``pick_fact``."""
    pick = pick_fact(unit_facts, fiscal_year, fiscal_period)
    return pick.fact, pick.status == DataStatus.CONFLICTING


# --------------------------------------------------------------------------
# Special cases
# --------------------------------------------------------------------------

Cache = Optional[dict]  # memoises tag resolution across the many concepts/years of one ingestion


def _resolved(cache: Cache, concept: str, company_facts: dict[str, Any]):
    if cache is None:
        return resolve_concept(concept, company_facts)
    key = ("concept", concept)
    if key not in cache:
        cache[key] = resolve_concept(concept, company_facts)
    return cache[key]


def _resolved_tag(cache: Cache, tag: str, company_facts: dict[str, Any]):
    if cache is None:
        return resolve_tags("component", [tag], company_facts)
    key = ("tag", tag)
    if key not in cache:
        cache[key] = resolve_tags("component", [tag], company_facts)
    return cache[key]


def _annual_anchor(company_facts: dict[str, Any], fiscal_year: int, cache: Cache = None) -> Optional[dict[str, Any]]:
    """The earliest-filed annual-report fact for the fiscal year: it identifies the ORIGINAL
    10-K (its accession number) and the fiscal year end date. Later filings that repeat the
    period as a comparative column are ignored."""
    for concept in ANCHOR_CONCEPTS:
        match = _resolved(cache, concept, company_facts)
        if not match.matched or not match.facts:
            continue
        annual = [
            f
            for f in _period_candidates(match.facts, fiscal_year, "FY")
            if str(f.get("form", "")).startswith(ANNUAL_FORM_PREFIXES)
        ]
        if annual:
            return min(annual, key=lambda f: f.get("filed", ""))
    return None


def _pick_shares(
    match_facts: list[dict[str, Any]], company_facts: dict[str, Any], fiscal_year: int, fiscal_period: str, cache: Cache = None
) -> Pick:
    """Share count for a fiscal year.

    The cover-page count (dei:EntityCommonStockSharesOutstanding) is dated after the year
    end, so for a December year end it falls in the NEXT calendar year. Matching it by that
    date's year (the generic rule) returned the PREVIOUS year's cover instead: for FY2025,
    JPM, GE and Intel came back with their FY2024 10-K counts (Intel 4,330M instead of
    4,995M). Instead the cover fact is taken from the original annual filing for the fiscal
    year, found through that year's income-statement facts; if that filing has no cover
    fact, the balance-sheet count at the fiscal year end is used, then the generic path.
    """
    if fiscal_period != "FY":
        return pick_fact(match_facts, fiscal_year, fiscal_period)
    anchor = _annual_anchor(company_facts, fiscal_year, cache)
    if anchor is not None:
        accn = anchor.get("accn")
        covers = [f for f in match_facts if f.get("_xbrl_tag") == SHARES_COVER_TAG and f.get("accn") == accn]
        if covers:
            newest = max(covers, key=lambda f: f.get("filed", ""))
            if len({c.get("val") for c in covers}) > 1:
                return Pick(newest, DataStatus.CONFLICTING, _restatement_note(newest, covers))
            return Pick(newest, DataStatus.REPORTED, f"Cover-page share count as of {newest.get('end')}.")
        balance = [
            f
            for f in match_facts
            if f.get("_xbrl_tag") == SHARES_BALANCE_SHEET_TAG and f.get("accn") == accn and f.get("end") == anchor.get("end")
        ]
        if balance:
            newest = max(balance, key=lambda f: f.get("filed", ""))
            return Pick(newest, DataStatus.REPORTED, f"Balance-sheet share count at {newest.get('end')} (no cover-page count in this filing).")
        match_facts = [f for f in match_facts if f.get("_xbrl_tag") != SHARES_COVER_TAG]
    return pick_fact(match_facts, fiscal_year, fiscal_period)


@dataclass
class _Derived:
    value: float
    anchor: dict[str, Any]
    tags: list[str]
    status: DataStatus
    note: str


def _component(tag: str, company_facts: dict[str, Any], fiscal_year: int, fiscal_period: str, cache: Cache = None) -> Optional[Pick]:
    match = _resolved_tag(cache, tag, company_facts)
    if not match.matched or not match.facts:
        return None
    pick = pick_fact(match.facts, fiscal_year, fiscal_period)
    return pick if pick.fact is not None else None


def _derive_total_debt(company_facts: dict[str, Any], fiscal_year: int, fiscal_period: str, cache: Cache = None) -> Optional[_Derived]:
    """total_debt as the sum of REPORTED components, when no single tag reports it.

    non-current debt + current debt, where current debt is ``DebtCurrent`` if reported,
    otherwise the sum of whichever of commercial paper / short-term borrowings / current
    portion of long-term debt are reported. Needs a non-current component AND at least one
    current component (a zero counts); with either side absent it returns None rather than
    guess that the missing side is zero.
    """
    noncurrent = next(
        ((tag, p) for tag in TOTAL_DEBT_NONCURRENT_TAGS if (p := _component(tag, company_facts, fiscal_year, fiscal_period, cache))),
        None,
    )
    if noncurrent is None:
        return None
    parts: list[tuple[str, Pick]] = [noncurrent]
    whole_current = _component(TOTAL_DEBT_CURRENT_TOTAL_TAG, company_facts, fiscal_year, fiscal_period, cache)
    if whole_current is not None:
        parts.append((TOTAL_DEBT_CURRENT_TOTAL_TAG, whole_current))
    else:
        for tag in TOTAL_DEBT_CURRENT_PARTS:
            p = _component(tag, company_facts, fiscal_year, fiscal_period, cache)
            if p is not None:
                parts.append((tag, p))
    if len(parts) < 2:
        return None

    total = sum(p.fact["val"] for _, p in parts)
    status = DataStatus.CONFLICTING if any(p.status == DataStatus.CONFLICTING for _, p in parts) else DataStatus.DERIVED
    note = "Derived: sum of reported components " + " + ".join(f"{tag} {_fmt(p.fact['val'])}" for tag, p in parts) + f" = {_fmt(total)}."
    accns = {p.fact.get("accn") for _, p in parts}
    if len(accns) > 1:
        note += " The components come from different filings: " + ", ".join(sorted(str(a) for a in accns)) + "."
    for tag, p in parts:
        if p.status == DataStatus.CONFLICTING and p.note:
            note += f" [{tag}] {p.note}"
    return _Derived(value=total, anchor=noncurrent[1].fact, tags=[t for t, _ in parts], status=status, note=note)


# --------------------------------------------------------------------------
# Building FinancialFact rows
# --------------------------------------------------------------------------


def _source_url(cik10: Optional[str], accession: Optional[str]) -> Optional[str]:
    if accession and cik10:
        return f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accession.replace('-', '')}/"
    return None


def build_fact(
    company_id: int,
    concept: str,
    company_facts: dict[str, Any],
    fiscal_year: int,
    fiscal_period: str = "FY",
    filing_id: Optional[int] = None,
    cik10: Optional[str] = None,
    cache: Cache = None,
) -> FinancialFact:
    """One FinancialFact for (concept, fiscal year, period), with provenance and an honest status.

    ``cache`` (any dict, reused across calls for the same ``company_facts``) avoids re-resolving
    the same tags for every concept/year of a multi-year ingestion.
    """
    period = f"{fiscal_year}-{fiscal_period}"
    match = _resolved(cache, concept, company_facts)

    def base(**overrides: Any) -> FinancialFact:
        fields: dict[str, Any] = dict(
            company_id=company_id,
            filing_id=filing_id,
            concept=concept,
            value=None,
            unit=match.unit or "USD",
            currency="USD",
            period=period,
            fiscal_year=fiscal_year,
            fiscal_period=fiscal_period,
            filing_date=None,
            source=SEC_SOURCE,
            source_url=None,
            accession_number=None,
            xbrl_tag=match.tag,
            data_status=DataStatus.MISSING,
            status_note=None,
        )
        fields.update(overrides)
        return FinancialFact(**fields)

    pick = Pick(None)
    if match.matched and match.facts:
        if concept == "shares_outstanding":
            pick = _pick_shares(match.facts, company_facts, fiscal_year, fiscal_period, cache)
        else:
            pick = pick_fact(match.facts, fiscal_year, fiscal_period, concept)

    if pick.fact is None and concept == "total_debt":
        derived = _derive_total_debt(company_facts, fiscal_year, fiscal_period, cache)
        if derived is not None:
            return base(
                value=derived.value,
                unit="USD",
                filing_date=_parse_date(derived.anchor.get("filed")),
                source_url=_source_url(cik10, derived.anchor.get("accn")),
                accession_number=derived.anchor.get("accn"),
                xbrl_tag="derived:" + "+".join(derived.tags),
                data_status=derived.status,
                status_note=derived.note,
            )

    if pick.fact is None:
        return base()

    entry = pick.fact
    return base(
        value=entry.get("val"),
        filing_date=_parse_date(entry.get("filed")),
        source_url=_source_url(cik10, entry.get("accn")),
        accession_number=entry.get("accn"),
        xbrl_tag=entry.get("_xbrl_tag", match.tag),
        data_status=pick.status,
        status_note=pick.note,
    )


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
    cache: dict = {}
    return [
        build_fact(company_id, concept, company_facts, fiscal_year, fiscal_period, filing_id=filing_id, cik10=cik10, cache=cache)
        for concept in CANONICAL_CONCEPTS
    ]


def latest_available_fiscal_year(company_facts: dict[str, Any], fiscal_period: str = "FY") -> Optional[int]:
    """Best-effort scan across all concepts to find the most recent fiscal year with any data.

    Derives fiscal year from each fact's own `end` date, NOT SEC's raw `fy`
    metadata field -- see `_period_candidates` for why `fy` cannot be trusted for this.
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
