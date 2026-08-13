"""
Audit trail / versioning.

This package will provide a single write path for AuditLogEntry rows (used
by services, valuation, ai, and governance packages) and helpers for
computing ValuationRun version numbers. The audit log is append-only and
immutable. Empty in Phase 1.
"""
