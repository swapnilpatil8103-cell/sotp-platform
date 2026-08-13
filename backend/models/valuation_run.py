"""ValuationRun model — a versioned snapshot of a valuation calculation."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import SQLModel, Field, Relationship, Column, JSON

from .enums import ValuationMethod


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ValuationRun(SQLModel, table=True):
    """
    A single, versioned, deterministic valuation computation for a Company.

    inputs/outputs are stored as JSON so the exact computation can be
    reproduced; the actual math lives in backend/valuation/ (Phase 5+) and is
    never touched by AI.
    """

    __tablename__ = "valuation_run"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True, nullable=False)

    method: ValuationMethod
    version: int = Field(default=1, description="Monotonically increasing version per company+method")

    inputs: dict = Field(default_factory=dict, sa_column=Column(JSON))
    outputs: dict = Field(default_factory=dict, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    created_by: Optional[str] = Field(default=None, max_length=256, description="User identifier")

    company: "Company" = Relationship(back_populates="valuation_runs")
    assumption_decisions: List["AssumptionDecision"] = Relationship(back_populates="valuation_run")
