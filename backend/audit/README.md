# backend/audit

Audit trail / versioning. Single write path for AuditLogEntry rows, used by
services, valuation, ai, and governance packages; also computes ValuationRun
version numbers. Append-only and immutable. Implemented starting Phase 2+ as
other packages need it. Empty in Phase 1.
