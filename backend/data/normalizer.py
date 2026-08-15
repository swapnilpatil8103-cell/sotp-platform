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


def _pick_fact_for_period(
    unit_facts: list[dict[str, Any]],
    fiscal_year: int,
    fiscal_period: str,
) -> Optional[dict[str, Any]]:
    """Pick the XBRL fact entry matching the requested fiscal year + period (e.g. FY/Q1..Q4).

    XBRL "units" entries look like:
    {"end": "2023-09-30", "val": 123, "fy": 2023, "fp": "FY", "form": "10-K",
     "filed": "2023-11-03", "start": "2022-10-01", "accn": "0000320193-23-000106"}

    We match on fy/fp when present, preferring 10-K/10-Q forms (skips amendments
    like 10-K/A only if a non-amended match exists first... but SEC data doesn't
    always distinguish; we just take the first fy/fp match, most-recently filed).
    """
    matches = [f for f in unit_facts if f.get("fy") == fiscal_year and f.get("fp") == fiscal_period]
    if not matches:
        return None
    # Prefer the most recently filed value in case of duplicates/restatements.
    matches.sort(key=lambda f: f.get("filed", ""), reverse=True)
    return matches[0]


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

        fact_entry = _pick_fact_for_period(match.facts, fiscal_year, fiscal_period)

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
                xbrl_tag=match.tag,
                data_status=DataStatus.REPORTED,
            )
        )

    return results


def latest_available_fiscal_year(company_facts: dict[str, Any], fiscal_period: str = "FY") -> Optional[int]:
    """Best-effort scan across all concepts to find the most recent fiscal year with any data."""
    years: set[int] = set()
    for concept in CANONICAL_CONCEPTS:
        match = resolve_concept(concept, company_facts)
        if not match.matched or not match.facts:
            continue
        for f in match.facts:
            if f.get("fp") == fiscal_period and isinstance(f.get("fy"), int):
                years.add(f["fy"])
    if not years:
        return None
    return max(years)
