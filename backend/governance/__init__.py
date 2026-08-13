"""
AI governance / approval gate logic.

This package will mediate between AI recommendations (backend/ai/) and human
decisions, enforcing that no AI-proposed assumption is usable by the
valuation engine until a human has approved, overridden, or rejected it
(Phase 7). It writes AssumptionDecision rows and triggers AuditLogEntry
writes via backend/audit/. See docs/ai-governance.md for the full rule set.
Empty in Phase 1.
"""
