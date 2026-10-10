"""Orchestration: approved assumptions -> Phase 5 engine -> versioned ValuationRun.

This module does no valuation math of its own, but it never persists a result
it did not get from the engine. ``execute_valuation_run`` (a) enforces that
every assumption an execution consumes has a human decision behind it, (b)
hands the client's ``inputs`` to ``run_computation`` to be validated against
the method's input schema and run through the deterministic engine
server-side, (c) checks the approved assumption values against those inputs,
and only then (d) persists the validated inputs and the SERVER-COMPUTED
outputs as a new versioned ``ValuationRun``.

There is deliberately no ``outputs`` parameter: a persisted run's outputs are
always a function of its persisted inputs, so a client cannot save a
fabricated result under approved decision ids. Every check happens before the
first write, so a rejected run leaves no trace in the database.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from backend.audit.logger import ACTION_VALUATION_RUN_EXECUTED, actor_human, log_event
from backend.governance.run_computation import RunRejectedError, compute_run, verify_assumptions
from backend.models.assumption_decision import AssumptionDecision
from backend.models.enums import AssumptionStatus, ValuationMethod
from backend.models.valuation_run import ValuationRun


class UnapprovedAssumptionError(RunRejectedError):
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


def _load_approved_decisions(
    session: Session, decision_ids: list[int], *, company_id: int
) -> list[AssumptionDecision]:
    """The approval gate: resolve ids to rows, raising UnapprovedAssumptionError
    if any is missing, belongs to a different company, or is not
    APPROVED/OVERRIDDEN."""
    rows: list[AssumptionDecision] = []
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
        rows.append(row)
    return rows


def assemble_approved_assumptions(
    session: Session, decision_ids: list[int], *, company_id: int
) -> dict[str, float]:
    """Resolve a list of AssumptionDecision ids into {assumption_key: value},
    raising UnapprovedAssumptionError if any is missing, belongs to a
    different company, or is not APPROVED/OVERRIDDEN.

    This is the gate: callers MUST go through this function (or
    execute_valuation_run, which uses the same check) to turn decisions into
    usable numbers -- there is no other path from AssumptionDecision to a
    value that skips the status check.
    """
    rows = _load_approved_decisions(session, decision_ids, company_id=company_id)
    return {row.assumption_key: row.approved_value for row in rows}


def execute_valuation_run(
    session: Session,
    *,
    company_id: int,
    method: ValuationMethod,
    decision_ids: list[int],
    inputs: dict[str, Any],
    created_by: str,
    source_data_version: Optional[dict[str, Any]] = None,
) -> ValuationRun:
    """Compute and persist a new versioned ValuationRun, gated on approved
    assumptions.

    ``inputs`` is the client's claim of what to feed the engine; it is
    validated against the method's input schema and run through the
    deterministic engine HERE, and the engine's result is what gets stored as
    ``outputs``. In order, and all before the first write:

    1. every decision_id must be APPROVED/OVERRIDDEN for this company
       (``UnapprovedAssumptionError``) -- checked first, so an unapproved run
       is refused before any computation;
    2. ``inputs`` must validate and the engine must accept them
       (``InvalidRunInputsError``);
    3. each approved value whose ``assumption_key`` maps to an input field
       must equal that field (``AssumptionMismatchError``).

    The persisted inputs are the validated model (schema fields only) plus a
    server-built ``_governance_snapshot`` recording which decisions were used,
    which were verified against which input field, and which keys had no
    field to verify.
    """
    decisions = _load_approved_decisions(session, decision_ids, company_id=company_id)
    computed = compute_run(method, inputs)
    verified, unmapped = verify_assumptions(
        method,
        computed.model,
        [(row.id, row.assumption_key, row.approved_value) for row in decisions],
    )
    assumptions_used = {row.assumption_key: row.approved_value for row in decisions}
    outputs = computed.outputs

    version = _next_version(session, company_id, method)

    snapshot_inputs = dict(computed.inputs)
    snapshot_inputs["_governance_snapshot"] = {
        "assumption_decision_ids": decision_ids,
        "assumptions_used": assumptions_used,
        "assumptions_verified": verified,
        "assumptions_unmapped": unmapped,
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
    # Real engine field names (DcfInput/CompsInput/SotpResult/...), which is
    # what persisted runs now actually contain.
    "revenue_growth_rates",
    "ebit_margins",
    "chosen_multiple_value",
    "implied_price_per_share",
    "sum_of_segment_evs",
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
