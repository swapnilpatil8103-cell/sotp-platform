"""Deterministic business-type classification from SEC SIC code.

Purely rule-based: an explicit, auditable SIC-code-range -> category lookup
table, followed by a category -> eligible-valuation-methodologies lookup
table. No AI/LLM call happens here. This is the ruleset that a later AI
recommendation layer (Phase 6) will be checked against, never the other way
around.

SIC (Standard Industrial Classification) codes are a public, standardized
4-digit US government taxonomy (still used by SEC EDGAR company filings,
`sic` field in the submissions JSON -- see backend/services/sec_client.py).
The ranges below are the commonly-used SEC/finance-industry groupings, e.g.:
  - 6020-6099, 6712            commercial/national/state banks, bank holding cos
  - 6120-6199                  savings institutions / credit unions (-> Bank)
  - 6311, 6321, 6331, 6411     life/accident/fire/marine insurance, agents
  - 6798                       Real Estate Investment Trusts
  - 6500-6599 (excl. 6798)     real estate (not REIT-elected) -> Real Estate/Industrial-ish
  - 1000-1099, 1200-1299,
    1400-1499                  metal/coal/nonmetallic mineral mining
  - 1300-1399                  crude petroleum & natural gas / oil & gas services
  - 2900-2999                  petroleum refining (-> Energy)
  - 4900-4999                  electric/gas/sanitary utilities (-> Energy)
  - 2800-2899                  chemicals & allied products (-> Industrial)
  - 3400-3999 (general)        machinery/electronics/transportation equipment (-> Industrial)
  - 7370-7379                  computer services (software, data processing)
  - 3570-3579, 3670-3679       computer & office equipment, electronic components
  - 3826-3829, 8000-8099       lab instruments, health services (-> Healthcare)
  - 2800-2836, 8731            biological products, commercial physical/biological
    research (Healthcare subset handled below with more specific ranges)
  - 2000-2199, 2300-2399       food/tobacco/apparel (-> Consumer Staples)
  - 5200-5999                  retail trade (-> Consumer)
  - 4800-4899                  communications (-> Communication Services)
  - 7000-8999 (general svc)    services (-> Consumer, default)

This table intentionally favors well-known, defensible SIC ranges over
exhaustive coverage of all ~1,000 SIC codes; anything unmatched classifies as
"Other" rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SicRange:
    low: int
    high: int
    category: str


# Ordered: more specific / narrower ranges first so they win over broader
# catch-alls when overlapping.
SIC_RANGES: list[SicRange] = [
    # --- Financial Institution / Bank ---
    SicRange(6020, 6036, "Bank"),  # national/state commercial banks
    SicRange(6060, 6062, "Bank"),  # savings institutions, federally chartered
    SicRange(6090, 6099, "Bank"),  # functions related to depository banking
    SicRange(6120, 6199, "Bank"),  # savings & loan / credit unions / other lenders
    SicRange(6712, 6712, "Bank"),  # bank holding companies
    # --- Insurance ---
    SicRange(6300, 6399, "Insurance"),  # life/fire/marine/surety/accident insurance + agents
    # --- REIT ---
    SicRange(6798, 6798, "REIT"),
    # --- Mining ---
    SicRange(1000, 1099, "Mining"),  # metal mining
    SicRange(1200, 1299, "Mining"),  # coal mining
    SicRange(1400, 1499, "Mining"),  # mining & quarrying of nonmetallic minerals
    # --- Energy (oil & gas, refining, utilities) ---
    SicRange(1300, 1399, "Energy"),  # crude petroleum & natural gas
    SicRange(2900, 2999, "Energy"),  # petroleum refining
    SicRange(4900, 4999, "Energy"),  # electric/gas/sanitary services (utilities)
    # --- Healthcare ---
    SicRange(2830, 2836, "Healthcare"),  # drugs / biological products
    SicRange(3826, 3829, "Healthcare"),  # lab apparatus / instruments
    SicRange(3841, 3845, "Healthcare"),  # surgical/medical/dental instruments & equipment
    SicRange(8000, 8099, "Healthcare"),  # health services
    # --- Software / Technology ---
    SicRange(7370, 7379, "Software"),  # computer programming, data processing, prepackaged software
    SicRange(3570, 3579, "Technology"),  # computer & office equipment
    SicRange(3670, 3679, "Technology"),  # electronic components & accessories
    SicRange(3660, 3669, "Technology"),  # communications equipment
    # --- Communication Services ---
    SicRange(4800, 4899, "Communication Services"),
    # --- Consumer Staples ---
    SicRange(2000, 2199, "Consumer Staples"),  # food, beverages, tobacco
    SicRange(2300, 2399, "Consumer Staples"),  # apparel
    # --- Industrial ---
    SicRange(2800, 2899, "Industrial"),  # chemicals & allied products (ex. drugs above)
    SicRange(3200, 3299, "Industrial"),  # stone/clay/glass/concrete
    SicRange(3300, 3399, "Industrial"),  # primary metal industries
    SicRange(3400, 3599, "Industrial"),  # fabricated metal / industrial machinery
    SicRange(3700, 3799, "Industrial"),  # transportation equipment
    SicRange(4000, 4799, "Industrial"),  # transportation & warehousing
    # --- Consumer (retail trade) ---
    SicRange(5200, 5999, "Consumer"),
    # --- Growth / Early Stage ---
    SicRange(8731, 8734, "Growth/Early Stage"),  # commercial physical/biological research, testing labs
    SicRange(6770, 6770, "Growth/Early Stage"),  # blank checks (SPACs)
]

# Category -> valuation methodologies a business of that type is eligible
# for, expressed as an explicit auditable mapping. This is the deterministic
# rules engine; a later AI-recommendation layer (Phase 6) must be checked
# against these, never override them silently.
ELIGIBLE_METHODOLOGIES: dict[str, list[str]] = {
    "Bank": ["P/B", "P/E", "DDM"],
    "Financial Institution": ["P/B", "P/E", "DDM"],
    "Insurance": ["P/B", "P/E", "DDM"],
    "REIT": ["NAV", "P/FFO", "AFFO"],
    "Energy": ["EV/EBITDA", "NAV"],
    "Mining": ["EV/EBITDA", "NAV"],
    "Industrial": ["DCF", "EV/EBITDA", "Comps"],
    "Technology": ["DCF", "EV/Revenue", "EV/EBITDA"],
    "Software": ["DCF", "EV/Revenue", "EV/EBITDA"],
    "Healthcare": ["DCF", "EV/EBITDA", "Comps"],
    "Consumer": ["DCF", "EV/EBITDA", "Comps", "P/E"],
    "Consumer Staples": ["DCF", "EV/EBITDA", "Comps", "P/E"],
    "Communication Services": ["DCF", "EV/EBITDA", "Comps"],
    "Growth/Early Stage": ["DCF", "EV/Revenue"],
    "Conglomerate": ["SOTP", "DCF", "Comps"],
    "Other": ["DCF", "Comps"],
}

VALID_CATEGORIES = frozenset(ELIGIBLE_METHODOLOGIES.keys())


@dataclass(frozen=True)
class BusinessClassification:
    sic_code: Optional[str]
    category: str
    eligible_methodologies: list[str]
    matched_range: Optional[str]
    rationale: str


def _lookup_category(sic_int: int) -> tuple[str, Optional[str]]:
    for r in SIC_RANGES:
        if r.low <= sic_int <= r.high:
            range_label = f"{r.low}-{r.high}" if r.low != r.high else str(r.low)
            return r.category, range_label
    return "Other", None


def classify_business(sic_code: Optional[str | int]) -> BusinessClassification:
    """Classify a company into a business-type category and its eligible
    deterministic valuation methodologies, purely from its SEC SIC code.

    Returns category="Other" (never raises, never fabricates a category) if
    ``sic_code`` is missing, non-numeric, or doesn't match any known range.
    """
    sic_str = str(sic_code).strip() if sic_code is not None else None

    if not sic_str:
        return BusinessClassification(
            sic_code=None,
            category="Other",
            eligible_methodologies=ELIGIBLE_METHODOLOGIES["Other"],
            matched_range=None,
            rationale="No SIC code provided.",
        )

    try:
        sic_int = int(sic_str)
    except ValueError:
        return BusinessClassification(
            sic_code=sic_str,
            category="Other",
            eligible_methodologies=ELIGIBLE_METHODOLOGIES["Other"],
            matched_range=None,
            rationale=f"SIC code '{sic_str}' is not numeric.",
        )

    category, matched_range = _lookup_category(sic_int)
    methodologies = ELIGIBLE_METHODOLOGIES.get(category, ELIGIBLE_METHODOLOGIES["Other"])
    rationale = (
        f"SIC {sic_int} falls in range {matched_range} -> {category}."
        if matched_range
        else f"SIC {sic_int} did not match any known range; defaulted to Other."
    )

    return BusinessClassification(
        sic_code=sic_str,
        category=category,
        eligible_methodologies=methodologies,
        matched_range=matched_range,
        rationale=rationale,
    )
