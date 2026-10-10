"""Server-side validation and computation for governed valuation runs.

A governed run's persisted ``outputs`` must be something the server computed,
never something a client claimed. This module is the single place that turns a
client-supplied ``inputs`` dict into (a) a validated, normalized input snapshot
and (b) engine outputs produced by ``backend.valuation``:

1. ``inputs`` is validated against the method's own schema (``DcfInput``,
   ``CompsInput`` or ``SotpInput``). Unknown keys are rejected rather than
   dropped: a typo in an optional field (``exit_mutiple``) would otherwise
   silently fall out of the run while the caller believes it was applied, and
   an extra key such as ``enterprise_value`` could otherwise be smuggled into
   the stored snapshot.
2. The deterministic engine (``run_dcf`` / ``run_comps`` / ``run_sotp``) is run
   server-side on the validated inputs.
3. Assumption values the analyst approved (``AssumptionDecision.approved_value``)
   are checked against the matching input fields, so a run cannot be saved
   under an approval whose number it does not actually use.

Everything here happens before anything is written; any failure raises a
``RunRejectedError`` subclass and the caller persists nothing.

This module deliberately sits beside, not inside, ``approval.py``: approval.py
only records human decisions and never imports the engine (see
``test_governance_approval``); executing a run is this layer's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from backend.models.enums import ValuationMethod
from backend.schemas.valuation import CompsInput, DcfInput, SotpInput
from backend.valuation.comps import run_comps
from backend.valuation.dcf import run_dcf
from backend.valuation.sotp import run_sotp


class RunRejectedError(ValueError):
    """A governed valuation run was refused; nothing was persisted. The API
    layer maps this (and every subclass) to HTTP 400."""


class InvalidRunInputsError(RunRejectedError):
    """``inputs`` failed validation against the method's input schema, carried
    unknown keys, or the engine rejected them (e.g. WACC <= terminal growth)."""


class AssumptionMismatchError(RunRejectedError):
    """An input value does not equal the ``approved_value`` of a referenced
    AssumptionDecision whose ``assumption_key`` maps to that input field."""


_METHOD_MODELS: dict[ValuationMethod, tuple[type[BaseModel], Any]] = {
    ValuationMethod.DCF: (DcfInput, run_dcf),
    ValuationMethod.COMPS: (CompsInput, run_comps),
    ValuationMethod.SOTP: (SotpInput, run_sotp),
}

# assumption_key -> input field it governs, per method. Only keys listed here
# are verified against the inputs; any other approved key still passes the
# approval gate and is recorded as "unmapped" in the run's governance snapshot
# (e.g. ``value_unlock_action:*`` decisions, or a DCF key referenced on a
# COMPS run).
#
# The DCF keys are the vocabulary the AI assumption task proposes
# (``backend.ai.tasks.assumption_recommendation.SUPPORTED_ASSUMPTION_KEYS``).
# ``revenue_growth_rate`` / ``ebit_margin`` are single values while DcfInput
# holds per-year schedules, so they are verified as a flat rate: EVERY year of
# the schedule must equal the approved value. A genuinely varying schedule is
# not expressible by one approved number, so it must not reference such a
# decision. ``peer_multiple`` is the comps key the diff module already uses.
# No AI-proposable key maps onto a SotpInput field today, so SOTP has none.
ASSUMPTION_FIELD_MAP: dict[ValuationMethod, dict[str, str]] = {
    ValuationMethod.DCF: {
        "wacc": "wacc",
        "terminal_growth_rate": "terminal_growth_rate",
        "exit_multiple": "exit_multiple",
        "revenue_growth_rate": "revenue_growth_rates",
        "ebit_margin": "ebit_margins",
    },
    ValuationMethod.COMPS: {
        "peer_multiple": "chosen_multiple_value",
    },
    ValuationMethod.SOTP: {},
}


@dataclass(frozen=True)
class ComputedRun:
    """The server-validated input snapshot and server-computed outputs."""

    inputs: dict[str, Any]
    outputs: dict[str, Any]
    model: BaseModel


def _summarize_validation_error(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()
    )


def compute_run(method: ValuationMethod, raw_inputs: dict[str, Any]) -> ComputedRun:
    """Validate ``raw_inputs`` for ``method`` and run the deterministic engine.

    The returned ``inputs`` is the validated model re-dumped (so only schema
    fields can ever be stored); ``outputs`` is the engine result without its
    echoed copy of the inputs, which are already stored alongside.
    """
    input_model, engine = _METHOD_MODELS[method]

    unknown = sorted(set(raw_inputs) - set(input_model.model_fields))
    if unknown:
        raise InvalidRunInputsError(
            f"Unknown input field(s) for {method.value}: {unknown}. "
            f"inputs must contain only {input_model.__name__} fields."
        )

    try:
        validated = input_model.model_validate(raw_inputs)
    except ValidationError as exc:
        raise InvalidRunInputsError(
            f"inputs do not match {input_model.__name__}: {_summarize_validation_error(exc)}"
        ) from exc

    try:
        result = engine(validated)
    except ValueError as exc:
        raise InvalidRunInputsError(f"{method.value} engine rejected the inputs: {exc}") from exc

    return ComputedRun(
        inputs=validated.model_dump(mode="json"),
        outputs=result.model_dump(mode="json", exclude={"inputs"}),
        model=validated,
    )


def _values_match(actual: Any, approved: float) -> bool:
    if actual is None:
        return False
    values = actual if isinstance(actual, list) else [actual]
    return bool(values) and all(math.isclose(v, approved, rel_tol=1e-9, abs_tol=1e-12) for v in values)


def verify_assumptions(
    method: ValuationMethod,
    validated_inputs: BaseModel,
    decisions: list[tuple[int, str, Optional[float]]],
) -> tuple[dict[str, str], list[str]]:
    """Check each approved decision against the input field its key governs.

    ``decisions`` is ``(decision_id, assumption_key, approved_value)`` for every
    decision the run references. Each one is checked individually, so two
    decisions on the same key with different approved values can never both
    match and the run is refused.

    Returns ``(verified, unmapped)``: ``verified`` maps assumption_key -> the
    input field it was checked against; ``unmapped`` lists keys with no input
    field for this method (gated by approval but not value-checked).
    """
    field_map = ASSUMPTION_FIELD_MAP[method]
    verified: dict[str, str] = {}
    unmapped: set[str] = set()

    for decision_id, key, approved in decisions:
        field_name = field_map.get(key)
        if field_name is None:
            unmapped.add(key)
            continue
        if approved is None:
            raise AssumptionMismatchError(
                f"AssumptionDecision {decision_id} ({key}) has no approved_value to verify the inputs against."
            )
        actual = getattr(validated_inputs, field_name)
        if not _values_match(actual, approved):
            raise AssumptionMismatchError(
                f"inputs.{field_name}={actual!r} does not match approved_value={approved} of "
                f"AssumptionDecision {decision_id} ({key}); a run must use the value a human approved."
            )
        verified[key] = field_name

    return verified, sorted(unmapped)
