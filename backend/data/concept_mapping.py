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
        # Banks (e.g. JPM) tag total net revenue as Revenues AND RevenuesNetOfInterestExpense;
        # keep it as a last-resort fallback for filers that only use the latter.
        "RevenuesNetOfInterestExpense",
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
    # NON-CURRENT long-term debt only. Do not add "LongTermDebt" here: that tag is the TOTAL
    # including the current portion (e.g. GE FY2025: LongTermDebt 20,469M vs balance-sheet
    # long-term borrowings 18,808M, which GE tags LongTermDebtAndCapitalLeaseObligations), so
    # using it as a fallback double counts the current portion wherever the two are added.
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
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


# ---------------------------------------------------------------------------
# Special handling that is not a plain "first matching tag" lookup. The normalizer
# (backend/data/normalizer.py) applies these; they are listed here so the tag
# vocabulary lives in one place.
# ---------------------------------------------------------------------------

# The "total revenue" tag. A filer that reports BOTH it and a contract-revenue tag for the same
# period reports contract revenue as a SUBSET (Berkshire Hathaway FY2025: Revenues 371,444M =
# contract revenue 247,244M + other revenue 124,200M), so the larger total supersedes it.
REVENUE_TOTAL_TAG = "Revenues"
REVENUE_CONTRACT_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)

# total_debt is derived (DataStatus.DERIVED) from reported components when no single tag
# reports it: current part (DebtCurrent, or the sum of whichever of the parts are reported)
# plus the non-current part. Every part is a real reported fact; nothing is estimated.
TOTAL_DEBT_CURRENT_TOTAL_TAG = "DebtCurrent"
TOTAL_DEBT_CURRENT_PARTS = ("CommercialPaper", "ShortTermBorrowings", "LongTermDebtCurrent")
TOTAL_DEBT_NONCURRENT_TAGS = ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations")

# Cover-page (dei) share count, as of a date shortly AFTER the fiscal year end.
SHARES_COVER_TAG = "EntityCommonStockSharesOutstanding"
SHARES_BALANCE_SHEET_TAG = "CommonStockSharesOutstanding"


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

    Every candidate tag's entries are kept (none is discarded because another tag
    reported the same period): two tags may legitimately disagree for one period
    because one is a subset of the other (revenue from contracts with customers vs
    total revenues; DebtCurrent vs LongTermDebtCurrent). Choosing among them, and
    detecting restatements (one tag, one period, different values in different
    filings -> DataStatus.CONFLICTING), is done in `normalizer.pick_fact`, where the
    requested period is known.

    EARLIER BUG (fixed): this function used to collapse entries that shared
    (start, end, fp, accn) across tags, keeping only the highest-priority tag. That
    silently discarded Berkshire Hathaway's total `Revenues` (371,444M) in favour of
    the contract-revenue subset (247,244M) before any selection logic could see it.
    """
    return resolve_tags(concept, CONCEPT_TAG_CANDIDATES.get(concept, []), company_facts)


def resolve_tags(concept: str, candidates: list[str] | tuple[str, ...], company_facts: dict[str, Any]) -> ConceptMatch:
    """Resolve an explicit, ordered list of candidate tags (see ``resolve_concept``, which
    passes the canonical concept's list). Used directly for the special cases above."""
    facts_by_taxonomy = company_facts.get("facts", {})

    primary_tag: Optional[str] = None
    primary_taxonomy: Optional[str] = None
    primary_unit: Optional[str] = None
    all_facts: list[dict[str, Any]] = []
    seen: set[tuple] = set()

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
                # Only an exact repeat (same tag, period, filing AND value) is dropped. Different
                # tags are all kept, with their own _xbrl_tag/_tag_priority: they can legitimately
                # differ for the same period (a total vs a subset), and the selection step in
                # normalizer.pick_fact needs to see both. Cross-FILING differences for one tag
                # (restatements) are detected there too, where the chosen tag is known.
                key = (tag, entry.get("start"), entry.get("end"), entry.get("fp"), entry.get("accn"), entry.get("val"))
                if key in seen:
                    continue
                seen.add(key)
                enriched = dict(entry)
                enriched["_xbrl_tag"] = tag
                enriched["_tag_priority"] = priority
                all_facts.append(enriched)

            # Only try the first taxonomy that has data for this tag.
            break

    if primary_tag is None:
        return ConceptMatch(concept=concept, matched=False)

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
