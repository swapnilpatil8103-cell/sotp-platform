"""Canonical financial concepts and their candidate US-GAAP XBRL tags.

SEC XBRL "company facts" data is keyed by taxonomy (almost always ``us-gaap``,
occasionally ``dei`` or ``ifrs-full`` for foreign filers) and then by tag name.
Different companies/filers sometimes use different tags for economically the
same concept (e.g. ``Revenues`` vs ``RevenueFromContractWithCustomerExcludingAssessedTax``
after ASC 606 adoption). To normalize across companies we define, for each
canonical internal concept, an ordered list of candidate XBRL tags to try —
the first one with data for the requested period wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# Canonical internal concept names used throughout the platform.
CANONICAL_CONCEPTS: list[str] = [
    "revenue",
    "cost_of_revenue",
    "gross_profit",
    "operating_income",
    "pretax_income",
    "net_income",
    "depreciation",
    "amortization",
    "da",
    "capex",
    "cash",
    "marketable_securities",
    "total_debt",
    "current_debt",
    "long_term_debt",
    "shares_outstanding",
    "stock_compensation",
    "operating_cash_flow",
    "working_capital",
    "minority_interest",
    "investments",
    "equity_income",
]

# Ordered candidate US-GAAP tags per canonical concept. Order matters: earlier
# entries are preferred / more specific, later entries are broader fallbacks.
CONCEPT_TAG_CANDIDATES: dict[str, list[str]] = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ],
    "depreciation": [
        "DepreciationDepletionAndAmortization",
        "Depreciation",
        "DepreciationAndAmortization",
    ],
    "amortization": [
        "AmortizationOfIntangibleAssets",
        "AmortizationOfFiniteLivedIntangibleAssets",
    ],
    "da": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireProductiveAssets",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ],
    "marketable_securities": [
        "MarketableSecuritiesCurrent",
        "MarketableSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesCurrent",
        "ShortTermInvestments",
    ],
    "total_debt": [
        "DebtLongtermAndShorttermCombinedAmount",
        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
    ],
    "current_debt": [
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    "shares_outstanding": [
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ],
    "stock_compensation": [
        "ShareBasedCompensation",
        "AllocatedShareBasedCompensationExpense",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "working_capital": [
        "IncreaseDecreaseInOperatingCapital",
    ],
    "minority_interest": [
        "MinorityInterest",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "investments": [
        "LongTermInvestments",
        "OtherLongTermInvestments",
        "EquityMethodInvestments",
    ],
    "equity_income": [
        "IncomeLossFromEquityMethodInvestments",
    ],
}

# XBRL taxonomies to search, in order of preference.
TAXONOMIES = ("us-gaap", "ifrs-full", "dei")


@dataclass
class ConceptMatch:
    """Result of resolving a canonical concept against a company's XBRL facts."""

    concept: str
    matched: bool
    tag: Optional[str] = None
    taxonomy: Optional[str] = None
    unit: Optional[str] = None
    facts: Optional[list[dict[str, Any]]] = None  # raw XBRL "units" entries for the matched tag


def resolve_concept(concept: str, company_facts: dict[str, Any]) -> ConceptMatch:
    """Resolve a single canonical concept against a company's raw XBRL companyfacts JSON.

    ``company_facts`` is the JSON payload from
    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json, i.e. it has a
    top-level ``facts`` key mapping taxonomy -> tag -> {"units": {unit: [...]}}.

    BUG HISTORY: this used to try each candidate tag in priority order and
    return the FIRST one with ANY non-empty fact list anywhere in the
    company's history, then commit to that single tag's facts for the entire
    company. That's wrong when a company itself switches which XBRL tag it
    uses for a concept across its own filing history (common on ASC 606
    adoption, SEC/FASB guidance changes, or a filer just changing its
    disclosure tag) -- confirmed with real GOOGL data: Alphabet reported
    revenue under `RevenueFromContractWithCustomerExcludingAssessedTax` from
    ~2018 through FY2024, then switched to plain `Revenues` for its FY2025
    10-K. Locking onto the first tag made FY2025 revenue silently disappear
    even though it was sitting right there under another valid candidate tag.

    Fix: check EVERY candidate tag (in priority order, each taxonomy in
    order) that has ANY data, and MERGE their fact-entry lists into one
    combined list, so a caller filtering by fiscal period (see
    `normalizer._pick_fact_for_period`) sees the union of periods across a
    company's tag transitions, not just whichever tag happened to match
    first. Each merged entry carries its own `_xbrl_tag`/`_tag_priority` keys
    so provenance (which literal tag a given VALUE came from) survives the
    merge -- callers must read a fact entry's own `_xbrl_tag`, not
    `ConceptMatch.tag`, when recording per-value provenance.

    `ConceptMatch.tag`/`.taxonomy`/`.unit` are still populated from the
    highest-priority tag that had any data at all, for callers that just want
    a simple "which tag matched" answer (e.g. logging) and don't care about
    the merge.

    When the SAME period (same `start`+`end`+`fp`) is reported under two
    different tags, we deduplicate: if the values agree, we keep only the
    higher-priority tag's entry (not a real conflict, just tag overlap /
    duplicate coverage). If the values genuinely differ, we keep both entries
    (each still tagged `_tag_priority`) and mark them `_conflicting=True` so
    the caller can surface `DataStatus.CONFLICTING` instead of silently
    picking one -- no fabrication, no silent guessing.
    """
    candidates = CONCEPT_TAG_CANDIDATES.get(concept, [])
    facts_by_taxonomy = company_facts.get("facts", {})

    primary_tag: Optional[str] = None
    primary_taxonomy: Optional[str] = None
    primary_unit: Optional[str] = None
    merged: dict[tuple, dict[str, Any]] = {}  # period key -> chosen entry (lowest priority number wins on tie)

    for priority, tag in enumerate(candidates):
        for taxonomy in TAXONOMIES:
            taxonomy_facts = facts_by_taxonomy.get(taxonomy, {})
            tag_entry = taxonomy_facts.get(tag)
            if not tag_entry:
                continue
            units = tag_entry.get("units", {})
            if not units:
                continue
            # Prefer USD if present, else take whatever unit is available first.
            unit_key = "USD" if "USD" in units else next(iter(units))
            unit_facts = units.get(unit_key) or []
            if not unit_facts:
                continue

            if primary_tag is None:
                primary_tag, primary_taxonomy, primary_unit = tag, taxonomy, unit_key

            for entry in unit_facts:
                key = (entry.get("start"), entry.get("end"), entry.get("fp"), entry.get("accn"))
                enriched = dict(entry)
                enriched["_xbrl_tag"] = tag
                enriched["_tag_priority"] = priority
                existing = merged.get(key)
                if existing is None:
                    merged[key] = enriched
                    continue
                # Same (start, end, fp, accn) already merged from a different
                # tag -- shouldn't normally happen (accn differs across
                # filings) but keep the higher-priority one defensively.
                if enriched["_tag_priority"] < existing["_tag_priority"]:
                    merged[key] = enriched

            # Only try the first taxonomy that has data for this tag.
            break

    if primary_tag is None:
        return ConceptMatch(concept=concept, matched=False)

    # Second pass: detect genuine value conflicts between different tags for
    # the exact same reporting period (same start/end/fp, different accn --
    # i.e. two different filings/tags both claim to report this period).
    by_period: dict[tuple, list[dict[str, Any]]] = {}
    for entry in merged.values():
        period_key = (entry.get("start"), entry.get("end"), entry.get("fp"))
        by_period.setdefault(period_key, []).append(entry)

    all_facts: list[dict[str, Any]] = []
    for period_key, entries in by_period.items():
        distinct_vals = {e.get("val") for e in entries}
        if len(distinct_vals) > 1:
            for e in entries:
                e["_conflicting"] = True
        all_facts.extend(entries)

    return ConceptMatch(
        concept=concept,
        matched=True,
        tag=primary_tag,
        taxonomy=primary_taxonomy,
        unit=primary_unit,
        facts=all_facts,
    )


def resolve_all_concepts(company_facts: dict[str, Any]) -> dict[str, ConceptMatch]:
    """Resolve every canonical concept against a company's raw XBRL facts."""
    return {concept: resolve_concept(concept, company_facts) for concept in CANONICAL_CONCEPTS}
