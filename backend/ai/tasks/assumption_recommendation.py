"""Task: propose DCF/WACC assumption values as explicit PROPOSALS.

Never final values. Output is shaped to map 1:1 onto the existing
AssumptionDecision model's ai_* fields (backend/models/assumption_decision.py)
so Phase 7 governance can persist each proposal as a PENDING
AssumptionDecision row and drive the human approve/edit/reject flow. This
task does not construct or persist AssumptionDecision rows itself -- it only
returns the AI-side proposal data.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.json_utils import extract_json
from backend.ai.validation import validate_ai_numbers

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."

# The assumption keys this task is allowed to propose. Anything outside this
# set returned by the model is dropped, not trusted.
SUPPORTED_ASSUMPTION_KEYS = {
    "revenue_growth_rate",
    "ebit_margin",
    "wacc",
    "terminal_growth_rate",
    "exit_multiple",
}


class AssumptionProposal(BaseModel):
    """Shape mirrors AssumptionDecision.ai_* fields exactly, so Phase 7 can
    do `AssumptionDecision(**proposal.model_dump(), valuation_run_id=..., status=PENDING)`
    style construction without reshaping."""

    assumption_key: str
    ai_recommended_value: float
    ai_rationale: str
    ai_confidence: Optional[float] = None
    requires_human_approval: bool = True


class AssumptionRecommendationResult(BaseModel):
    ai_origin: bool = True
    abstained: bool
    proposals: list[AssumptionProposal] = []
    dropped_out_of_scope: list[str] = []
    validation_ok: Optional[bool] = None
    validation_notes: Optional[str] = None
    reason: Optional[str] = None


def _build_prompt(company_financials: dict[str, Any], peer_context: dict[str, Any]) -> str:
    return (
        "You are a valuation assumptions advisor. Given the company financials and "
        "peer/market context below, PROPOSE values (not final decisions -- a human "
        "will approve, edit, or reject each) for these assumption keys only: "
        f"{sorted(SUPPORTED_ASSUMPTION_KEYS)}. Base every proposal on the provided "
        "data; do not invent financial facts. Give a confidence score 0-1 for each.\n\n"
        f"Company financials: {company_financials}\n\n"
        f"Peer/market context: {peer_context}\n\n"
        'Reply as strict JSON: {"proposals": [{"assumption_key": "wacc", '
        '"recommended_value": 0.09, "rationale": "...", "confidence": 0.6}, ...]}'
    )


def recommend_assumptions(
    adapter: AIAdapter,
    company_financials: dict[str, Any],
    peer_context: Optional[dict[str, Any]] = None,
) -> AssumptionRecommendationResult:
    peer_context = peer_context or {}

    if not company_financials:
        return AssumptionRecommendationResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " No company financial data provided to ground assumptions.",
        )

    prompt = _build_prompt(company_financials, peer_context)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return AssumptionRecommendationResult(abstained=True, reason=f"AI unavailable: {exc}")

    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return AssumptionRecommendationResult(abstained=True, reason=f"Could not parse AI response: {exc}")

    if not isinstance(parsed, dict) or not parsed.get("proposals"):
        return AssumptionRecommendationResult(abstained=True, reason=ABSTAIN_TEXT + " AI returned no proposals.")

    proposals: list[AssumptionProposal] = []
    dropped: list[str] = []
    for p in parsed["proposals"]:
        if not isinstance(p, dict):
            continue
        key = p.get("assumption_key")
        if key not in SUPPORTED_ASSUMPTION_KEYS:
            if key:
                dropped.append(str(key))
            continue
        value = p.get("recommended_value")
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        proposals.append(
            AssumptionProposal(
                assumption_key=key,
                ai_recommended_value=value,
                ai_rationale=str(p.get("rationale", "")),
                ai_confidence=p.get("confidence"),
            )
        )

    if not proposals:
        return AssumptionRecommendationResult(
            abstained=True,
            dropped_out_of_scope=dropped,
            reason=ABSTAIN_TEXT + " No in-scope, numeric proposals parsed.",
        )

    # Guardrail: rationale text should not smuggle in fabricated numbers not
    # traceable to the input context (the proposed value itself is exempt --
    # it's explicitly a new proposal, not a restatement of an input fact).
    combined_rationale = " ".join(p.ai_rationale for p in proposals)
    validation = validate_ai_numbers(combined_rationale, {"financials": company_financials, "peers": peer_context})

    return AssumptionRecommendationResult(
        abstained=False,
        proposals=proposals,
        dropped_out_of_scope=dropped,
        validation_ok=validation.ok,
        validation_notes=validation.notes,
    )
