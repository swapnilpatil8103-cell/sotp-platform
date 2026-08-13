"""AssumptionDecision model — AI recommendation vs human-approved value."""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field, Relationship

from .enums import AssumptionStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AssumptionDecision(SQLModel, table=True):
    """
    The central artifact of human-in-the-loop governance (see
    docs/ai-governance.md). Records an AI-proposed assumption (e.g. a
    terminal growth rate) alongside the value a human ultimately approved or
    overrode, why, and who/when.
    """

    __tablename__ = "assumption_decision"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Nullable: a proposal is created (PENDING) before it is ever attached to
    # a persisted ValuationRun. It only gets a valuation_run_id once a human
    # decision has been recorded AND that decision was actually consumed by
    # a valuation run execution (backend/governance/valuation_run.py).
    valuation_run_id: Optional[int] = Field(default=None, foreign_key="valuation_run.id", index=True)

    # A proposal is scoped to a company from the moment it's proposed, even
    # before any ValuationRun exists, so pending/decided proposals for a
    # company can be listed and assembled into a run.
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)

    assumption_key: str = Field(max_length=128, description="e.g. terminal_growth_rate, wacc")
    subject: Optional[str] = Field(
        default=None,
        max_length=256,
        description="Human-readable label for what's being decided, e.g. 'Terminal growth rate' or 'Peer set: cloud comps'",
    )

    ai_recommended_value: Optional[float] = Field(default=None)
    ai_rationale: Optional[str] = Field(default=None, max_length=4096)
    ai_confidence: Optional[float] = Field(default=None, description="0-1 confidence score, if provided")

    approved_value: Optional[float] = Field(default=None)
    approval_reason: Optional[str] = Field(default=None, max_length=4096)

    status: AssumptionStatus = Field(default=AssumptionStatus.PENDING)

    decided_by: Optional[str] = Field(default=None, max_length=256, description="User identifier")
    decided_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)

    valuation_run: Optional["ValuationRun"] = Relationship(back_populates="assumption_decisions")
