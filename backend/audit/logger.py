"""Single write path for AuditLogEntry rows, plus chronological retrieval.

Every material event in the system (AI proposal generated, human decision
recorded, valuation run executed, data ingested) should go through
``log_event`` here rather than constructing ``AuditLogEntry`` rows ad hoc, so
there's exactly one place that defines what "actor" and "detail" shapes look
like. The log is append-only/immutable: nothing in this module ever updates
or deletes a row after creation.

``get_audit_trail`` reconstructs a chronological story for a company or a
valuation run, structured so a caller can walk CLAIM -> SOURCE -> INPUT ->
CALCULATION -> OUTPUT -> AI INTERPRETATION: each entry carries an
``action`` (what happened), an ``actor`` (who/what did it: "ai" | "human:<id>"
| "system"), and a ``detail`` payload that names the specific fields that
map onto those roles for that event type (see ``EVENT_FIELD_ROLES`` below).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from backend.models.audit_log_entry import AuditLogEntry

# Actor conventions used throughout this module and by callers.
ACTOR_AI = "ai"
ACTOR_SYSTEM = "system"


def actor_human(user_id: str) -> str:
    return f"human:{user_id}"


# Canonical action names. Not enforced (detail/action are free-form on the
# model) but centralized here so producers/consumers agree on spelling.
ACTION_AI_PROPOSAL_GENERATED = "AI_PROPOSAL_GENERATED"
ACTION_DECISION_RECORDED = "DECISION_RECORDED"
ACTION_VALUATION_RUN_EXECUTED = "VALUATION_RUN_EXECUTED"
ACTION_DATA_INGESTED = "DATA_INGESTED"

# For each action, which conceptual role (of the CLAIM/SOURCE/INPUT/
# CALCULATION/OUTPUT/AI_INTERPRETATION chain) the event primarily represents.
# Used only to annotate retrieval output -- purely descriptive.
EVENT_ROLE = {
    ACTION_DATA_INGESTED: "SOURCE",
    ACTION_AI_PROPOSAL_GENERATED: "AI_INTERPRETATION",
    ACTION_DECISION_RECORDED: "INPUT",
    ACTION_VALUATION_RUN_EXECUTED: "CALCULATION_AND_OUTPUT",
}


def log_event(
    session: Session,
    *,
    entity_type: str,
    entity_id: Optional[int],
    action: str,
    actor: str,
    detail: Optional[dict[str, Any]] = None,
) -> AuditLogEntry:
    """Write one immutable audit entry. Returns the persisted row."""
    entry = AuditLogEntry(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor=actor,
        detail=detail or {},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def get_audit_trail(
    session: Session,
    *,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Return a chronologically ordered (oldest first) audit trail.

    Callers typically pass either (entity_type="ValuationRun", entity_id=<id>)
    to see everything tied to one run, or (entity_type="Company",
    entity_id=<company_id>) / company_id=<id> to see the full history for a
    company across proposals/decisions/runs (entries whose ``detail`` payload
    carries a matching ``company_id`` are included too, since not every event
    -- e.g. a pending proposal -- is yet attached to a ValuationRun entity).
    """
    statement = select(AuditLogEntry)
    if entity_type is not None and entity_id is not None:
        statement = statement.where(
            AuditLogEntry.entity_type == entity_type,
            AuditLogEntry.entity_id == entity_id,
        )
    statement = statement.order_by(AuditLogEntry.created_at.asc(), AuditLogEntry.id.asc())
    rows = session.exec(statement).all()

    if company_id is not None and not (entity_type == "Company" and entity_id == company_id):
        # Also pull in anything logged elsewhere whose detail payload
        # references this company (e.g. AssumptionDecision proposals before
        # they're attached to a run).
        extra = session.exec(
            select(AuditLogEntry).order_by(AuditLogEntry.created_at.asc(), AuditLogEntry.id.asc())
        ).all()
        seen_ids = {r.id for r in rows}
        for r in extra:
            if r.id in seen_ids:
                continue
            if r.detail.get("company_id") == company_id:
                rows.append(r)
        rows.sort(key=lambda r: (r.created_at, r.id))

    return [
        {
            "id": r.id,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "action": r.action,
            "role": EVENT_ROLE.get(r.action, "OTHER"),
            "actor": r.actor,
            "detail": r.detail,
            "created_at": r.created_at,
        }
        for r in rows
    ]
