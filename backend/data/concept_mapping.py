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

    Tries each candidate tag (in order) across each taxonomy (in order) and
    returns the first one present in the company's facts. Does not filter by
    fiscal period here — that's the caller's job once a tag is chosen.
    """
    candidates = CONCEPT_TAG_CANDIDATES.get(concept, [])
    facts_by_taxonomy = company_facts.get("facts", {})

    for tag in candidates:
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
            return ConceptMatch(
                concept=concept,
                matched=True,
                tag=tag,
                taxonomy=taxonomy,
                unit=unit_key,
                facts=unit_facts,
            )

    return ConceptMatch(concept=concept, matched=False)


def resolve_all_concepts(company_facts: dict[str, Any]) -> dict[str, ConceptMatch]:
    """Resolve every canonical concept against a company's raw XBRL facts."""
    return {concept: resolve_concept(concept, company_facts) for concept in CANONICAL_CONCEPTS}
