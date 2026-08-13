"""Shared enums used across the ORM models."""

import enum


class DataStatus(str, enum.Enum):
    """Provenance/quality status of a financial data point."""

    REPORTED = "REPORTED"
    DERIVED = "DERIVED"
    ESTIMATED = "ESTIMATED"
    MISSING = "MISSING"
    CONFLICTING = "CONFLICTING"


class FilingType(str, enum.Enum):
    """SEC filing form types relevant to valuation."""

    FORM_10K = "10-K"
    FORM_10Q = "10-Q"
    FORM_8K = "8-K"
    FORM_20F = "20-F"
    FORM_DEF14A = "DEF 14A"
    OTHER = "OTHER"


class ValuationMethod(str, enum.Enum):
    """Supported deterministic valuation methods (see docs/valuation-methodology.md)."""

    DCF = "DCF"
    COMPS = "COMPS"
    SOTP = "SOTP"


class AssumptionStatus(str, enum.Enum):
    """Lifecycle status of an AI-proposed assumption pending human review."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    OVERRIDDEN = "OVERRIDDEN"
    REJECTED = "REJECTED"
