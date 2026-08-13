"""Pydantic request/response schemas for the governance API surface
(backend/api/routers/valuation.py's proposal/decision/run-history endpoints).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ProposeAssumptionRequest(BaseModel):
    company_id: int
    assumption_key: str
    ai_recommended_value: float
    ai_rationale: str
    ai_confidence: Optional[float] = None
    subject: Optional[str] = None


class AssumptionDecisionOut(BaseModel):
    id: int
    valuation_run_id: Optional[int]
    company_id: Optional[int]
    assumption_key: str
    subject: Optional[str]
    ai_recommended_value: Optional[float]
    ai_rationale: Optional[str]
    ai_confidence: Optional[float]
    approved_value: Optional[float]
    approval_reason: Optional[str]
    status: str
    decided_by: Optional[str]
    decided_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class RecordDecisionRequest(BaseModel):
    decision: str = Field(..., description="APPROVE | EDIT | REJECT")
    user_id: str
    human_value: Optional[float] = Field(default=None, description="Required for EDIT")
    reason: Optional[str] = None


class RunValuationRequest(BaseModel):
    company_id: int
    method: str = Field(..., description="DCF | COMPS | SOTP")
    decision_ids: list[int] = Field(..., description="AssumptionDecision ids to consume; each must be APPROVED/OVERRIDDEN")
    inputs: dict[str, Any] = Field(..., description="Full input snapshot fed to the Phase 5 engine (already assembled by caller)")
    outputs: dict[str, Any] = Field(..., description="Phase 5 engine result, already computed by caller")
    created_by: str
    source_data_version: Optional[dict[str, Any]] = None


class ValuationRunOut(BaseModel):
    id: int
    company_id: int
    method: str
    version: int
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    created_at: datetime
    created_by: Optional[str]

    model_config = {"from_attributes": True}


class DiffResponse(BaseModel):
    company_id: int
    from_version: int
    to_version: int
    method_from: str
    method_to: str
    changed_fields: list[dict[str, Any]]
    unchanged_field_count: int
    key_changes: list[dict[str, Any]]
    summary: str


class AuditTrailEntryOut(BaseModel):
    id: int
    entity_type: str
    entity_id: Optional[int]
    action: str
    role: str
    actor: Optional[str]
    detail: dict[str, Any]
    created_at: datetime
