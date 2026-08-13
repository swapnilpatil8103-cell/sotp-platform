"""The human judgment gate.

Workflow: AI proposes (via a backend/ai/tasks task) -> a human APPROVES,
EDITS, or REJECTS -> the decision is persisted as an ``AssumptionDecision``
row. This module performs no financial calculation whatsoever -- it only
records decisions. Actual valuation execution (Phase 5's deterministic
engine) is invoked separately, in ``backend/governance/valuation_run.py``,
which enforces (see that module) that every assumption it consumes has a
non-PENDING ``AssumptionDecision`` behind it.

Both the AI-original value/rationale and the human-final value/reason remain
visible on the same row forever -- ``record_decision`` never overwrites
``ai_recommended_value``/``ai_rationale``, it only ever sets the
``approved_value``/``approval_reason``/``status``/``decided_by``/
``decided_at`` fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from backend.audit.logger import (
    ACTION_AI_PROPOSAL_GENERATED,
    ACTION_DECISION_RECORDED,
    ACTOR_AI,
    actor_human,
    log_event,
)
from backend.models.assumption_decision import AssumptionDecision
from backend.models.enums import AssumptionStatus


class GovernanceError(ValueError):
    """Raised for invalid governance operations (e.g. deciding twice)."""


def propose(
    session: Session,
    *,
    company_id: int,
    assumption_key: str,
    ai_recommended_value: float,
    ai_rationale: str,
    ai_confidence: Optional[float] = None,
    subject: Optional[str] = None,
) -> AssumptionDecision:
    """Create a PENDING AssumptionDecision from an AI task's output.

    Not attached to any ValuationRun yet -- that only happens once the run
    that actually consumes it is executed (see valuation_run.py). Logs an
    AI_PROPOSAL_GENERATED audit event with actor="ai".
    """
    decision = AssumptionDecision(
        company_id=company_id,
        assumption_key=assumption_key,
        subject=subject or assumption_key,
        ai_recommended_value=ai_recommended_value,
        ai_rationale=ai_rationale,
        ai_confidence=ai_confidence,
        status=AssumptionStatus.PENDING,
    )
    session.add(decision)
    session.commit()
    session.refresh(decision)

    log_event(
        session,
        entity_type="AssumptionDecision",
        entity_id=decision.id,
        action=ACTION_AI_PROPOSAL_GENERATED,
        actor=ACTOR_AI,
        detail={
            "company_id": company_id,
            "assumption_key": assumption_key,
            "ai_recommended_value": ai_recommended_value,
            "ai_rationale": ai_rationale,
            "ai_confidence": ai_confidence,
        },
    )
    return decision


def record_decision(
    session: Session,
    *,
    decision_id: int,
    decision: str,
    user_id: str,
    human_value: Optional[float] = None,
    reason: Optional[str] = None,
) -> AssumptionDecision:
    """Human submits APPROVE / EDIT / REJECT for a pending proposal.

    - APPROVE: ``approved_value`` is set to the AI's original recommended
      value; ``status`` -> APPROVED.
    - EDIT: caller must supply ``human_value``; ``status`` -> OVERRIDDEN,
      ``approval_reason`` should explain the edit.
    - REJECT: no assumption value survives for downstream use; ``status`` ->
      REJECTED. ``reason`` should explain why.

    The AI's original ``ai_recommended_value``/``ai_rationale`` fields are
    never mutated by this function -- only the human_* / status / decided_*
    fields are written, so both remain visible on the same row afterward.

    Raises GovernanceError if the decision has already been decided (no
    re-deciding a row -- create a new proposal instead) or for an unknown
    ``decision`` value / a missing ``human_value`` on EDIT.
    """
    row = session.get(AssumptionDecision, decision_id)
    if row is None:
        raise GovernanceError(f"No AssumptionDecision with id={decision_id}")
    if row.status != AssumptionStatus.PENDING:
        raise GovernanceError(
            f"AssumptionDecision {decision_id} already decided (status={row.status}); "
            "create a new proposal instead of re-deciding."
        )

    decision_upper = decision.upper()
    if decision_upper == "APPROVE":
        row.status = AssumptionStatus.APPROVED
        row.approved_value = row.ai_recommended_value
        row.approval_reason = reason
    elif decision_upper == "EDIT":
        if human_value is None:
            raise GovernanceError("EDIT requires human_value")
        row.status = AssumptionStatus.OVERRIDDEN
        row.approved_value = human_value
        row.approval_reason = reason
    elif decision_upper == "REJECT":
        row.status = AssumptionStatus.REJECTED
        row.approved_value = None
        row.approval_reason = reason
    else:
        raise GovernanceError(f"Unknown decision '{decision}' (expected APPROVE/EDIT/REJECT)")

    row.decided_by = user_id
    row.decided_at = datetime.now(timezone.utc)
    session.add(row)
    session.commit()
    session.refresh(row)

    log_event(
        session,
        entity_type="AssumptionDecision",
        entity_id=row.id,
        action=ACTION_DECISION_RECORDED,
        actor=actor_human(user_id),
        detail={
            "company_id": row.company_id,
            "assumption_key": row.assumption_key,
            "decision": decision_upper,
            "ai_recommended_value": row.ai_recommended_value,
            "human_final_value": row.approved_value,
            "reason": reason,
        },
    )
    return row


def get_decision(session: Session, decision_id: int) -> Optional[AssumptionDecision]:
    return session.get(AssumptionDecision, decision_id)


def list_pending_for_company(session: Session, company_id: int) -> list[AssumptionDecision]:
    return list(
        session.exec(
            select(AssumptionDecision).where(
                AssumptionDecision.company_id == company_id,
                AssumptionDecision.status == AssumptionStatus.PENDING,
            )
        ).all()
    )


def list_decisions_for_company(session: Session, company_id: int) -> list[AssumptionDecision]:
    return list(
        session.exec(
            select(AssumptionDecision)
            .where(AssumptionDecision.company_id == company_id)
            .order_by(AssumptionDecision.created_at.asc())
        ).all()
    )
