"""
SQLModel ORM models for SOTP Intelligence.

Every persisted financial data point traces back to a source SEC filing
(accession number, XBRL tag, source URL) via FinancialFact / SegmentFinancialFact.
AssumptionDecision and AuditLogEntry implement the human-in-the-loop governance
model described in docs/ai-governance.md.
"""

from .enums import (
    DataStatus,
    FilingType,
    ValuationMethod,
    AssumptionStatus,
)
from .company import Company
from .filing import Filing
from .financial_fact import FinancialFact
from .segment import Segment
from .segment_financial_fact import SegmentFinancialFact
from .valuation_run import ValuationRun
from .assumption_decision import AssumptionDecision
from .audit_log_entry import AuditLogEntry
from .market_data_snapshot import MarketDataSnapshot
from .insider_transaction import InsiderTransaction
from .institutional_holding import InstitutionalHolding

__all__ = [
    "DataStatus",
    "FilingType",
    "ValuationMethod",
    "AssumptionStatus",
    "Company",
    "Filing",
    "FinancialFact",
    "Segment",
    "SegmentFinancialFact",
    "ValuationRun",
    "AssumptionDecision",
    "AuditLogEntry",
    "MarketDataSnapshot",
    "InsiderTransaction",
    "InstitutionalHolding",
]
