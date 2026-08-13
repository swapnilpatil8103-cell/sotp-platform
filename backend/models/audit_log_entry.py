"""AuditLogEntry model — immutable append-only audit trail."""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field, Column, JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditLogEntry(SQLModel, table=True):
    """
    Immutable, append-only record of a meaningful action in the system
    (data fetch, valuation run, assumption decision, override, AI call).
    Never updated or deleted after creation.
    """

    __tablename__ = "audit_log_entry"

    id: Optional[int] = Field(default=None, primary_key=True)

    entity_type: str = Field(max_length=64, description="e.g. Company, Filing, ValuationRun, AssumptionDecision")
    entity_id: Optional[int] = Field(default=None, index=True)

    action: str = Field(max_length=128, description="e.g. FETCHED, COMPUTED, APPROVED, OVERRIDDEN, AI_CALLED")
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))

    actor: Optional[str] = Field(default=None, max_length=256, description="User identifier or 'system'/'ai'")
    created_at: datetime = Field(default_factory=_utcnow, nullable=False, index=True)
