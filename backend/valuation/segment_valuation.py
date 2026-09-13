"""Automated per-segment valuation.

Given a segment's REPORTED financial facts and an explicit, human-supplied
methodology + assumption set, produces a SUGGESTED segment enterprise value
by composing the EXISTING deterministic engine functions
(``backend.valuation.dcf.run_dcf`` / ``backend.valuation.comps.run_comps``)
at segment level -- this module invents no new valuation math, it only
assembles/dispatches inputs correctly per segment.

Two supported methodologies, chosen by the caller (never inferred/guessed):

- ``"multiple"``: apply a caller-supplied EV/Revenue or EV/EBIT multiple
  directly to the segment's own REPORTED revenue/EBIT
  (``segment_ev = segment_metric * multiple``). This is mechanically the
  same "apply a chosen multiple to a metric" arithmetic
  ``backend.valuation.comps`` already performs for the EV-based metrics --
  reused here directly rather than reimplemented, via
  ``comps.compute_peer_multiples``'s sibling maths is not re-derived; we call
  through a one-line application matching ``run_comps``'s own
  ``implied_enterprise_value = target_metric_value * multiple`` formula.
- ``"dcf"``: run the existing ``run_dcf`` using segment-level assumption
  inputs (growth/margins/wacc/etc, all explicit human-supplied assumptions)
  and the segment's own REPORTED ``base_revenue``.

If a segment lacks the REPORTED data required for the chosen method (e.g. no
revenue fact for an EV/Revenue multiple, or a missing base_revenue for DCF),
the segment comes back with a clear per-segment error/reason -- the function
never fabricates a number or silently falls back to a different method.

Every result is tagged ``SUGGESTED`` and explicitly marked as requiring human
review before it may pre-fill (never bypass) the SOTP flow -- see
``backend/governance/approval.py`` for the propose/approve pattern this
should be routed through before entering a persisted ``ValuationRun``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.schemas.valuation import DcfInput, DcfResult

MultipleMetric = Literal["ev_to_revenue", "ev_to_ebit"]


class SegmentMultipleAssumption(BaseModel):
    """Explicit, human-supplied multiple assumption for the 'multiple' method."""

    metric: MultipleMetric
    multiple_value: float = Field(..., description="Explicit multiple to apply, e.g. peer-median EV/Revenue or EV/EBIT")


class SegmentDcfAssumption(BaseModel):
    """Explicit, human-supplied DCF assumption set for the 'dcf' method.

    Mirrors DcfInput but omits base_revenue (taken from the segment's own
    REPORTED revenue fact instead, never re-typed by the caller) and the
    EV->equity bridge fields net_debt/cash/investments/minority_interest/
    diluted_shares_outstanding, which are not meaningful at the segment level
    -- segment valuation stops at enterprise value; SOTP (upstream of this
    module) applies the company-level bridge once across all segments.
    """

    revenue_growth_rates: list[float] = Field(..., min_length=1)
    ebit_margins: list[float] = Field(..., min_length=1)
    tax_rate: float
    da_pct_of_revenue: list[float] = Field(..., min_length=1)
    capex_pct_of_revenue: list[float] = Field(..., min_length=1)
    nwc_change_pct_of_revenue: list[float] = Field(..., min_length=1)
    wacc: float
    terminal_growth_rate: float
    exit_multiple: Optional[float] = None


class SegmentFacts(BaseModel):
    """The segment's own REPORTED financial facts (from segment_extractor.py /
    SegmentFinancialFact rows upstream) -- this module only reads them, never
    writes/derives new facts."""

    name: str
    revenue: Optional[float] = None
    revenue_status: str = "MISSING"  # DataStatus value as string, e.g. "REPORTED"
    ebit: Optional[float] = None
    ebit_status: str = "MISSING"


class SegmentValuationRequest(BaseModel):
    segment: SegmentFacts
    methodology: Literal["multiple", "dcf"]
    multiple_assumption: Optional[SegmentMultipleAssumption] = None
    dcf_assumption: Optional[SegmentDcfAssumption] = None


class SegmentValuationSuggestion(BaseModel):
    segment_name: str
    methodology: Literal["multiple", "dcf"]
    status: Literal["SUGGESTED", "ERROR"] = "SUGGESTED"
    error: Optional[str] = None
    suggested_enterprise_value: Optional[float] = None
    dcf_result: Optional[DcfResult] = None
    data_status: str = Field(
        "SUGGESTED",
        description="Always SUGGESTED for a successful result -- never REPORTED/APPROVED. A human must review/approve before this value enters a persisted ValuationRun.",
    )
    requires_review: bool = True


def _value_from_multiple(segment: SegmentFacts, assumption: SegmentMultipleAssumption) -> tuple[Optional[float], Optional[str]]:
    if assumption.metric == "ev_to_revenue":
        metric_value, status, label = segment.revenue, segment.revenue_status, "revenue"
    else:  # ev_to_ebit
        metric_value, status, label = segment.ebit, segment.ebit_status, "EBIT"

    if metric_value is None or status != "REPORTED":
        return None, (
            f"Segment '{segment.name}' has no REPORTED {label} fact required for the '{assumption.metric}' "
            f"multiple method (status={status}); refusing to fabricate or fall back to a different method."
        )
    # Same formula backend.valuation.comps.run_comps applies for EV-based
    # metrics: implied_enterprise_value = target_metric_value * multiple.
    return metric_value * assumption.multiple_value, None


def _value_from_dcf(segment: SegmentFacts, assumption: SegmentDcfAssumption) -> tuple[Optional[DcfResult], Optional[str]]:
    if segment.revenue is None or segment.revenue_status != "REPORTED":
        return None, (
            f"Segment '{segment.name}' has no REPORTED base revenue required to run a segment-level DCF "
            f"(status={segment.revenue_status}); refusing to fabricate a base_revenue."
        )

    dcf_input = DcfInput(
        base_revenue=segment.revenue,
        revenue_growth_rates=assumption.revenue_growth_rates,
        ebit_margins=assumption.ebit_margins,
        tax_rate=assumption.tax_rate,
        da_pct_of_revenue=assumption.da_pct_of_revenue,
        capex_pct_of_revenue=assumption.capex_pct_of_revenue,
        nwc_change_pct_of_revenue=assumption.nwc_change_pct_of_revenue,
        wacc=assumption.wacc,
        terminal_growth_rate=assumption.terminal_growth_rate,
        # Segment-level EV only: bridge fields are neutralized (0) since SOTP
        # applies the company-level bridge once, upstream of this module.
        net_debt=0.0,
        cash_and_equivalents=0.0,
        investments=0.0,
        minority_interest=0.0,
        diluted_shares_outstanding=1.0,
        exit_multiple=assumption.exit_multiple,
    )
    try:
        dcf_input.validate_lengths()
    except ValueError as exc:
        return None, str(exc)

    # Reuse the existing deterministic engine directly -- no new DCF math.
    from backend.valuation.dcf import run_dcf

    try:
        result = run_dcf(dcf_input)
    except ValueError as exc:
        return None, str(exc)
    return result, None


def value_segment(request: SegmentValuationRequest) -> SegmentValuationSuggestion:
    """Produce a SUGGESTED enterprise value for one segment, or a clear
    per-segment error if the chosen method's required data is missing."""
    segment = request.segment

    if request.methodology == "multiple":
        if request.multiple_assumption is None:
            return SegmentValuationSuggestion(
                segment_name=segment.name,
                methodology=request.methodology,
                status="ERROR",
                error="methodology='multiple' requires multiple_assumption to be supplied.",
                requires_review=True,
            )
        ev, error = _value_from_multiple(segment, request.multiple_assumption)
        if error:
            return SegmentValuationSuggestion(
                segment_name=segment.name, methodology=request.methodology, status="ERROR", error=error, requires_review=True
            )
        return SegmentValuationSuggestion(
            segment_name=segment.name,
            methodology=request.methodology,
            status="SUGGESTED",
            suggested_enterprise_value=ev,
            requires_review=True,
        )

    # methodology == "dcf"
    if request.dcf_assumption is None:
        return SegmentValuationSuggestion(
            segment_name=segment.name,
            methodology=request.methodology,
            status="ERROR",
            error="methodology='dcf' requires dcf_assumption to be supplied.",
            requires_review=True,
        )
    result, error = _value_from_dcf(segment, request.dcf_assumption)
    if error:
        return SegmentValuationSuggestion(
            segment_name=segment.name, methodology=request.methodology, status="ERROR", error=error, requires_review=True
        )
    return SegmentValuationSuggestion(
        segment_name=segment.name,
        methodology=request.methodology,
        status="SUGGESTED",
        suggested_enterprise_value=result.enterprise_value,
        dcf_result=result,
        requires_review=True,
    )


class AutoValueSegmentsRequest(BaseModel):
    segments: list[SegmentValuationRequest] = Field(..., min_length=1)


class AutoValueSegmentsResponse(BaseModel):
    suggestions: list[SegmentValuationSuggestion]
    governance_note: str = (
        "Every value in this response is AI/model-SUGGESTED, not a reported fact or an approved assumption. "
        "It is intended to pre-fill the SOTP segment EV fields for human review -- a human must review/edit/"
        "approve before any of these figures are computed into a persisted ValuationRun (see "
        "backend/governance/approval.py)."
    )


def value_segments(request: AutoValueSegmentsRequest) -> AutoValueSegmentsResponse:
    return AutoValueSegmentsResponse(suggestions=[value_segment(r) for r in request.segments])
