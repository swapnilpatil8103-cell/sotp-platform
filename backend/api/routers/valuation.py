"""Valuation router.

Phase 5 adds one minimal, low-risk real endpoint: POST /valuation/sotp, which
takes a fully-assembled SotpInput (segment EVs, ownership, non-operating
assets, debt, overhead treatment -- all supplied by the caller) and returns
the computed SotpResult. It does not persist a ValuationRun or resolve a
ticker's own data -- that wiring (assembling inputs from real Company/
Segment/MarketDataSnapshot data, writing ValuationRun rows) is deferred; see
docs/implementation-plan.md Phase 5 section for what's deferred and why.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlmodel import Session

from backend.api.deps import AuthenticatedUser, get_current_user, require_analyst
from backend.audit.logger import get_audit_trail
from backend.db import get_session
from backend.governance import approval
from backend.governance.run_computation import RunRejectedError
from backend.governance.valuation_run import (
    diff_runs,
    execute_valuation_run,
    get_version,
    list_versions,
)
from backend.models.enums import ValuationMethod
from backend.schemas.governance import (
    AssumptionDecisionOut,
    AuditTrailEntryOut,
    DiffResponse,
    ProposeAssumptionRequest,
    RecordDecisionRequest,
    RunValuationRequest,
    ValuationRunOut,
)
from backend.schemas.valuation import (
    CompsInput,
    CompsResult,
    DcfInput,
    DcfResult,
    DcfScalarField,
    DcfSensitivityOutput,
    SensitivityResult,
    SotpInput,
    SotpResult,
    WaccInput,
    WaccResult,
)
from backend.services.macro_data_client import MacroDataClient, MacroDataError
from backend.valuation.beta_analysis import BetaAnalysisInput, BetaAnalysisResult, run_beta_analysis
from backend.valuation.comps import run_comps
from backend.valuation.dcf import run_dcf
from backend.valuation.segment_valuation import (
    AutoValueSegmentsRequest,
    AutoValueSegmentsResponse,
    value_segments,
)
from backend.valuation.sensitivity import dcf_sensitivity
from backend.valuation.sotp import run_sotp
from backend.valuation.wacc import compute_wacc

router = APIRouter(prefix="/valuation", tags=["valuation"])


@router.post("/sotp", response_model=SotpResult)
def compute_sotp(payload: SotpInput):
    """Compute a Sum-of-the-Parts valuation from a fully-assembled input set.

    Pure deterministic computation -- no DB read/write, no persistence of a
    ValuationRun. Callers are expected to have already assembled segment
    enterprise values (via DCF/comps run separately) and non-operating
    balance sheet figures.
    """
    try:
        return run_sotp(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/dcf", response_model=DcfResult)
def compute_dcf(payload: DcfInput):
    """Added in Phase 9 (frontend integration): the DCF tab needs a real
    endpoint over backend.valuation.dcf.run_dcf, which -- like comps below --
    previously had no router at all (only /valuation/sotp existed). Same
    pure-compute, no-persistence convention as /valuation/sotp."""
    try:
        payload.validate_lengths()
        return run_dcf(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/comps", response_model=CompsResult)
def compute_comps(payload: CompsInput):
    """Added in Phase 9 (frontend integration): the Comps tab needs a real
    endpoint over backend.valuation.comps.run_comps. Same pure-compute,
    no-persistence convention as /valuation/sotp."""
    try:
        return run_comps(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# A grid re-runs the DCF once per cell, so each axis is capped (25 x 25 = 625 runs).
MAX_SENSITIVITY_AXIS_VALUES = 25


class DcfSensitivityRequestPayload(BaseModel):
    """Axis and output names are restricted to real scalar fields: an unknown
    name used to be silently ignored for an axis (a constant grid) and raised an
    uncaught AttributeError for the output (a 500)."""

    model_config = ConfigDict(allow_inf_nan=False)

    base_dcf_input: dict
    row_field: DcfScalarField
    row_values: list[float] = Field(..., min_length=1, max_length=MAX_SENSITIVITY_AXIS_VALUES)
    col_field: DcfScalarField
    col_values: list[float] = Field(..., min_length=1, max_length=MAX_SENSITIVITY_AXIS_VALUES)
    output_field: DcfSensitivityOutput = "implied_price_per_share"

    @model_validator(mode="after")
    def _axes_must_differ(self) -> "DcfSensitivityRequestPayload":
        # With the same field on both axes the column value overwrites the row
        # value, so the grid would silently vary only one input.
        if self.row_field == self.col_field:
            raise ValueError("row_field and col_field must be different fields")
        return self


@router.post("/sensitivity/dcf", response_model=SensitivityResult)
def compute_dcf_sensitivity(payload: DcfSensitivityRequestPayload):
    """Added in Phase 9 (frontend integration): the Sensitivities dashboard
    page needs a real endpoint over backend.valuation.sensitivity.dcf_sensitivity,
    which previously had no router wired to it at all. Pure computation, no
    persistence -- same convention as POST /valuation/sotp above.
    """
    try:
        return dcf_sensitivity(
            payload.base_dcf_input,
            payload.row_field,
            payload.row_values,
            payload.col_field,
            payload.col_values,
            payload.output_field,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{ticker}/segments/auto-value", response_model=AutoValueSegmentsResponse)
def auto_value_segments(ticker: str, payload: AutoValueSegmentsRequest):
    """Automated per-segment valuation suggestions (multiple or DCF method,
    per segment) over the real segment list + explicit human-supplied
    methodology/assumption choices. Composes the existing run_dcf/run_comps-
    style arithmetic at segment level -- no new valuation math.

    Every returned figure is tagged SUGGESTED / requires_review=True and is
    meant to pre-fill (never bypass) the SOTP segment EV fields on the
    frontend; nothing here writes an AssumptionDecision or ValuationRun. A
    segment missing the REPORTED data its chosen method needs comes back
    with status="ERROR" and a clear reason, never a fabricated value.
    `ticker` is accepted for URL/routing symmetry with the rest of this API
    (matching /companies/{ticker}/...) but this endpoint is pure compute over
    the caller-supplied segment list, like /valuation/sotp and /valuation/dcf.
    """
    return value_segments(payload)


@router.post("/wacc/peer-beta", response_model=BetaAnalysisResult)
def compute_peer_informed_beta(payload: BetaAnalysisInput):
    """Unlever each peer's levered beta (Hamada), aggregate (median/average),
    then relever at the target's own D/E and tax rate -- a SUGGESTED,
    peer-informed beta for the WACC/Ke input. Pure deterministic math, no AI.

    Peers missing real debt_to_equity/tax_rate are skipped and reported
    (never fabricated). The result never substitutes into a WACC computation
    automatically -- callers/frontend must let a human review it before using
    it as the `beta` field on POST /valuation/wacc (not itself wired here;
    see backend/valuation/wacc.py for that pure computation).
    """
    try:
        return run_beta_analysis(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/wacc/risk-free-rate-suggestion")
def suggest_risk_free_rate(symbol: str = "US10Y"):
    """Latest available Treasury yield (London Strategic Edge vault API) as a
    SUGGESTED risk-free rate for the WACC cost-of-equity input -- a sourced
    starting point, not an authoritative or real-time rate. A human must
    still choose to use it; nothing here substitutes into a WACC computation
    automatically, exactly like the peer-informed-beta suggestion above.

    Returns 503 if LSE_API_KEY isn't configured or the provider is
    unreachable -- callers should fall back to a manual risk-free-rate input
    in that case, never a fabricated rate.
    """
    try:
        result = MacroDataClient().get_risk_free_rate_suggestion(symbol)
    except MacroDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "symbol": result.symbol,
        "rate_pct": result.rate_pct,
        "as_of_date": result.as_of_date,
        "source": result.source,
        "fetched_at": result.fetched_at,
        "label": "SUGGESTED",
    }


@router.post("/wacc", response_model=WaccResult)
def compute_wacc_endpoint(payload: WaccInput):
    """Thin pass-through to backend.valuation.wacc.compute_wacc: a real endpoint
    for submitting reviewed/edited WACC inputs (e.g. after pre-filling a
    peer-informed beta), same pure-compute-no-persistence convention as
    /valuation/sotp and /valuation/dcf.

    The body is a typed `WaccInput`, so a malformed or out-of-range field (e.g.
    a tax_rate outside [0, 1)) is a standard 422 with per-field detail.
    """
    try:
        return compute_wacc(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{ticker}/run")
def run_valuation(ticker: str):
    # TODO(Phase 5+ follow-up): resolve `ticker` -> company_id automatically.
    # Full ticker-driven data assembly (pulling FinancialFact/Segment/
    # MarketDataSnapshot straight from persisted data) remains deferred; use
    # POST /valuation/run below (company_id-based) for the Phase 7 governed
    # flow, which the frontend/callers can pair with a ticker->company_id
    # lookup via GET /companies/{ticker}.
    raise HTTPException(
        status_code=501,
        detail="Not implemented: ticker-driven auto data assembly is deferred; use POST /valuation/run with an explicit company_id",
    )


@router.get("/{ticker}/runs")
def list_valuation_runs(ticker: str):
    raise HTTPException(
        status_code=501,
        detail="Not implemented: ticker-driven lookup is deferred; use GET /valuation/company/{company_id}/runs",
    )


# --------------------------------------------------------------------------
# Phase 7 — governance: proposals, decisions, governed run execution,
# version history, diff, audit trail.
# --------------------------------------------------------------------------


@router.post("/assumptions/propose", response_model=AssumptionDecisionOut)
def propose_assumption(
    payload: ProposeAssumptionRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Create a PENDING AssumptionDecision from an AI task's output (Phase 6).

    This endpoint itself does not call the AI -- callers run a
    backend.ai.tasks task first (e.g. recommend_assumptions) and pass its
    proposal fields here to be recorded as a governance artifact. The caller
    is recorded as `proposed_by` and can never be the one to decide it.
    """
    decision = approval.propose(
        session,
        company_id=payload.company_id,
        assumption_key=payload.assumption_key,
        ai_recommended_value=payload.ai_recommended_value,
        ai_rationale=payload.ai_rationale,
        ai_confidence=payload.ai_confidence,
        subject=payload.subject,
        proposed_by=user.subject,
    )
    return decision


@router.post("/assumptions/{decision_id}/decide", response_model=AssumptionDecisionOut)
def decide_assumption(
    decision_id: int,
    payload: RecordDecisionRequest,
    user: AuthenticatedUser = Depends(require_analyst),
    session: Session = Depends(get_session),
):
    """A human submits APPROVE / EDIT / REJECT for a pending proposal.

    Requires the `analyst` role. The approver recorded in `decided_by` and in
    the audit-log actor is the authenticated token subject; the request body
    cannot name one.
    """
    try:
        return approval.record_decision(
            session,
            decision_id=decision_id,
            decision=payload.decision,
            user_id=user.subject,
            human_value=payload.human_value,
            reason=payload.reason,
        )
    except approval.SeparationOfDutiesError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except approval.GovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/company/{company_id}/assumptions", response_model=list[AssumptionDecisionOut])
def list_assumptions(company_id: int, session: Session = Depends(get_session)):
    return approval.list_decisions_for_company(session, company_id)


def _parse_method(raw: str) -> ValuationMethod:
    """Case-insensitive ValuationMethod, or a 400 listing the valid values (a bare
    ValueError from the enum constructor would surface as a 500)."""
    try:
        return ValuationMethod(raw.upper())
    except ValueError as exc:
        valid = ", ".join(m.value for m in ValuationMethod)
        raise HTTPException(status_code=400, detail=f"Unknown method '{raw}'; expected one of: {valid}") from exc


@router.post("/run", response_model=ValuationRunOut)
def run_governed_valuation(
    payload: RunValuationRequest,
    user: AuthenticatedUser = Depends(require_analyst),
    session: Session = Depends(get_session),
):
    """Compute and persist a new versioned ValuationRun, gated on approved
    assumptions.

    Requires the `analyst` role; `created_by` and the audit-log actor are the
    authenticated token subject (the body cannot name one).

    The server never trusts a client-supplied result: the request carries no
    `outputs` (sending one is a 422), `inputs` is validated against
    DcfInput / CompsInput / SotpInput for the method, the deterministic engine
    is run server-side, and the persisted `outputs` are what the engine
    returned. Responds 400, writing nothing, if:

    - any id in `decision_ids` is not an APPROVED/OVERRIDDEN decision for
      `company_id` (checked first);
    - `inputs` fail validation, contain unknown keys, or are rejected by the
      engine;
    - an approved value whose `assumption_key` maps to an input field (see
      `backend/governance/run_computation.py::ASSUMPTION_FIELD_MAP`) differs
      from that input.
    """
    method = _parse_method(payload.method)

    try:
        return execute_valuation_run(
            session,
            company_id=payload.company_id,
            method=method,
            decision_ids=payload.decision_ids,
            inputs=payload.inputs,
            created_by=user.subject,
            source_data_version=payload.source_data_version,
        )
    except RunRejectedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/company/{company_id}/runs", response_model=list[ValuationRunOut])
def list_company_runs(company_id: int, method: str | None = None, session: Session = Depends(get_session)):
    method_enum = _parse_method(method) if method else None
    return list_versions(session, company_id, method_enum)


@router.get("/company/{company_id}/runs/diff", response_model=DiffResponse)
def diff_company_runs(
    company_id: int,
    from_version: int,
    to_version: int,
    method: str | None = None,
    session: Session = Depends(get_session),
):
    method_enum = _parse_method(method) if method else None
    run_a = get_version(session, company_id, from_version, method_enum)
    run_b = get_version(session, company_id, to_version, method_enum)
    if run_a is None or run_b is None:
        raise HTTPException(status_code=404, detail="One or both versions not found")
    return diff_runs(run_a, run_b)


@router.get("/company/{company_id}/audit-trail", response_model=list[AuditTrailEntryOut])
def company_audit_trail(company_id: int, session: Session = Depends(get_session)):
    return get_audit_trail(session, company_id=company_id)


@router.get("/runs/{valuation_run_id}/audit-trail", response_model=list[AuditTrailEntryOut])
def run_audit_trail(valuation_run_id: int, session: Session = Depends(get_session)):
    return get_audit_trail(session, entity_type="ValuationRun", entity_id=valuation_run_id)
