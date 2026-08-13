"""
Data normalization layer.

This package will convert raw SEC/XBRL responses fetched by backend/services/
into the internal FinancialFact / SegmentFinancialFact shape (Phase 2-3),
resolving units, periods, and conflicting sources, and assigning the
appropriate DataStatus. Empty in Phase 1.
"""
