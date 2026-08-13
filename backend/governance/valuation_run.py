"""Orchestration: approved assumptions -> Phase 5 engine -> versioned ValuationRun.

This module never computes valuation math itself; it only (a) enforces that
every assumption an execution consumes has a human decision behind it, (b)
calls into ``backend.valuation`` (already computed by the caller -- see
``execute_valuation_run``'s ``outputs`` param) is out of scope here too; this
module's job is strictly gating + persisting + versioning + diffing.

The actual call into e.g. ``backend.valuation.sotp.run_sotp`` happens in the
API layer (``backend/api/routers/valuation.py``), which assembles a
``SotpInput`` (or DCF/comps input) using the approved values pulled from
governance, invokes the Phase 5 engine, and passes the resulting
inputs/outputs dicts here to persist as a new ``ValuationRun`` version. That
keeps this module free of any dependency on the shape of any one
methodology's input/result schema.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from backend.audit.logger import ACTION_VALUATION_RUN_EXECUTED, actor_human, log_event
from backend.models.assumption_decision import AssumptionDecision
from backend.models.enums import AssumptionStatus, ValuationMethod
from backend.models.valuation_run import ValuationRun


class UnapprovedAssumptionError(ValueError):
    """Raised when a valuation run is attempted using an assumption that has
    no human decision (still PENDING) or was REJECTED. This is the concrete
    enforcement mechanism for "no AI-only auto-execution": every
    decision_id passed to execute_valuation_run must resolve to a row whose
    status is APPROVED or OVERRIDDEN -- never PENDING, never REJECTED, and
    it must belong to the company the run is for.
    """


def _next_version(session: Session, company_id: int, method: ValuationMethod) -> int:
    existing = session.exec(
        select(ValuationRun.version).where(
            ValuationRun.company_id == company_id,
            ValuationRun.method == method,
        )
    ).all()
    return (max(existing) if existing else 0) + 1


def assemble_approved_assumptions(
    session: Session, decision_ids: list[int], *, company_id: int
) -> dict[str, float]:
    """Resolve a list of AssumptionDecision ids into {assumption_key: value},
    raising UnapprovedAssumptionError if any is missing, belongs to a
    different company, or is not APPROVED/OVERRIDDEN.

    This is the gate: callers (the API layer) MUST go through this function
    (or execute_valuation_run, which calls it) to turn decisions into usable
    numbers -- there is no other path from AssumptionDecision to a value
    that skips the status check.
    """
    assumptions: dict[str, float] = {}
    for decision_id in decision_ids:
        row = session.get(AssumptionDecision, decision_id)
        if row is None:
            raise UnapprovedAssumptionError(f"No AssumptionDecision with id={decision_id}")
        if row.company_id != company_id:
            raise UnapprovedAssumptionError(
                f"AssumptionDecision {decision_id} belongs to company_id={row.company_id}, not {company_id}"
            )
        if row.status not in (AssumptionStatus.APPROVED, AssumptionStatus.OVERRIDDEN):
            raise UnapprovedAssumptionError(
                f"AssumptionDecision {decision_id} ({row.assumption_key}) has status={row.status}; "
                "a human must APPROVE or EDIT it before it can be used in a valuation run."
            )
        assumptions[row.assumption_key] = row.approved_value
    return assumptions


def execute_valuation_run(
    session: Session,
    *,
    company_id: int,
    method: ValuationMethod,
    decision_ids: list[int],
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    created_by: str,
    source_data_version: Optional[dict[str, Any]] = None,
) -> ValuationRun:
    """Persist a new versioned ValuationRun, gated on approved assumptions.

    ``inputs`` should be the full snapshot of what was fed to the Phase 5
    engine (already computed by the caller using
    ``assemble_approved_assumptions``'s output plus peer set / methodology /
    source-data references); ``outputs`` is the engine's result, already
    computed by the caller. This function re-validates decision_ids itself
    (does not trust the caller to have checked) before persisting anything,
    and records which company/peer-set/methodology/human-decision snapshot
    produced this run inside ``inputs["_governance_snapshot"]``.
    """
    # Re-validate here too (not just trust the caller) -- this is the actual
    # enforcement point: no ValuationRun is ever written without every
    # decision_id checking out as APPROVED/OVERRIDDEN for this company.
    assumptions_used = assemble_approved_assumptions(session, decision_ids, company_id=company_id)

    version = _next_version(session, company_id, method)

    snapshot_inputs = dict(inputs)
    snapshot_inputs["_governance_snapshot"] = {
        "assumption_decision_ids": decision_ids,
        "assumptions_used": assumptions_used,
        "source_data_version": source_data_version or {},
    }

    run = ValuationRun(
        company_id=company_id,
        method=method,
        version=version,
        inputs=snapshot_inputs,
        outputs=outputs,
        created_by=created_by,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Attach the decisions used to this run (visible from either side).
    for decision_id in decision_ids:
        row = session.get(AssumptionDecision, decision_id)
        if row is not None:
            row.valuation_run_id = run.id
            session.add(row)
    session.commit()

    log_event(
        session,
        entity_type="ValuationRun",
        entity_id=run.id,
        action=ACTION_VALUATION_RUN_EXECUTED,
        actor=actor_human(created_by),
        detail={
            "company_id": company_id,
            "method": method.value if hasattr(method, "value") else str(method),
            "version": version,
            "assumption_decision_ids": decision_ids,
            "assumptions_used": assumptions_used,
            "outputs_summary": outputs,
        },
    )
    return run


def list_versions(session: Session, company_id: int, method: Optional[ValuationMethod] = None) -> list[ValuationRun]:
    statement = select(ValuationRun).where(ValuationRun.company_id == company_id)
    if method is not None:
        statement = statement.where(ValuationRun.method == method)
    statement = statement.order_by(ValuationRun.version.asc())
    return list(session.exec(statement).all())


def get_version(session: Session, company_id: int, version: int, method: Optional[ValuationMethod] = None) -> Optional[ValuationRun]:
    statement = select(ValuationRun).where(
        ValuationRun.company_id == company_id, ValuationRun.version == version
    )
    if method is not None:
        statement = statement.where(ValuationRun.method == method)
    return session.exec(statement).first()


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------

_KEY_FIELDS_OF_INTEREST = (
    "revenue_growth_rate",
    "ebit_margin",
    "wacc",
    "terminal_growth_rate",
    "exit_multiple",
    "peer_multiple",
    "sotp_value",
    "per_share_value",
    "implied_share_price",
    "enterprise_value",
    "equity_value",
)


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict into dotted-key -> scalar, skipping lists of
    objects (kept as a single repr'd value) so the diff stays readable."""
    flat: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                flat.update(_flatten(v, key))
            else:
                flat[key] = v
    else:
        flat[prefix] = d
    return flat


def diff_runs(run_a: ValuationRun, run_b: ValuationRun) -> dict[str, Any]:
    """Deterministic, template-based diff between two ValuationRun versions
    for the same company. No AI involved. Surfaces every changed field
    (inputs and outputs) plus a highlighted subset of well-known
    key-assumption/key-output fields for a human-readable summary.
    """
    if run_a.company_id != run_b.company_id:
        raise ValueError("diff_runs requires two runs for the same company")

    flat_a = {**_flatten(run_a.inputs, "inputs"), **_flatten(run_a.outputs, "outputs")}
    flat_b = {**_flatten(run_b.inputs, "inputs"), **_flatten(run_b.outputs, "outputs")}

    all_keys = sorted(set(flat_a) | set(flat_b))
    changed: list[dict[str, Any]] = []
    unchanged_count = 0
    for key in all_keys:
        old = flat_a.get(key)
        new = flat_b.get(key)
        if old != new:
            changed.append({"field": key, "from": old, "to": new})
        else:
            unchanged_count += 1

    highlights = [
        c for c in changed if any(c["field"].endswith(f".{k}") or c["field"] == k for k in _KEY_FIELDS_OF_INTEREST)
    ]

    return {
        "company_id": run_a.company_id,
        "from_version": run_a.version,
        "to_version": run_b.version,
        "method_from": run_a.method.value if hasattr(run_a.method, "value") else str(run_a.method),
        "method_to": run_b.method.value if hasattr(run_b.method, "value") else str(run_b.method),
        "changed_fields": changed,
        "unchanged_field_count": unchanged_count,
        "key_changes": highlights,
        "summary": (
            f"{len(changed)} field(s) changed between v{run_a.version} and v{run_b.version} "
            f"({len(highlights)} key assumption/output change(s))."
            if changed
            else f"No differences between v{run_a.version} and v{run_b.version}."
        ),
    }
