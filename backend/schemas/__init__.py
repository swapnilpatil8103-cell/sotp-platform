"""
Pydantic request/response schemas.

Stub for Phase 1. Real schemas (CompanyRead, FilingRead, ValuationRunRead,
AssumptionDecisionCreate, etc.) will be added alongside each router as it is
implemented (see docs/implementation-plan.md, Phases 2-8). Keeping schemas
separate from backend/models/ ORM classes lets the API surface evolve
independently of the persisted schema.
"""
