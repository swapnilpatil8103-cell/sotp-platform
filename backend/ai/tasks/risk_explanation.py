"""Task: AI-assisted scoring for Strategic Risk and Execution Risk.

These two risk categories are less quantifiable than the other four in
``backend.valuation.risk_dashboard`` (Data/Model/Forecast/Market Risk, all
rule-based from real system inputs), so they are AI-flagged instead. Critically,
the AI's output is constrained to the LOW/MEDIUM/HIGH enum -- any response
that doesn't map cleanly onto that enum for BOTH categories is discarded
(ABSTAIN), never coerced or free-text. This is the concrete guardrail
described in the phase spec: AI proposes a categorical judgment, not a
number, and even that judgment is validated against a small closed set
before it is trusted.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.json_utils import extract_json
from backend.schemas.valuation import RiskCategoryScore, RiskLevel

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."

_VALID_LEVELS = {"LOW", "MEDIUM", "HIGH"}


class RiskExplanationResult(BaseModel):
    ai_origin: bool = True
    abstained: bool
    strategic_risk: Optional[RiskCategoryScore] = None
    execution_risk: Optional[RiskCategoryScore] = None
    reason: Optional[str] = None


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "You are assessing Strategic Risk and Execution Risk for a company, based on the "
        "structured context below (SOTP breakdown, conglomerate discount, business "
        "category, any proposed structural actions). You MUST answer with EXACTLY one of "
        "LOW, MEDIUM, or HIGH for each category -- no other words, no free text, no "
        "additional nuance in the score field itself (put nuance in the explanation).\n\n"
        f"Context: {context}\n\n"
        'Reply as strict JSON: {"strategic_risk": {"score": "LOW", "explanation": "..."}, '
        '"execution_risk": {"score": "MEDIUM", "explanation": "..."}}'
    )


def _validated_level(raw: Any) -> Optional[RiskLevel]:
    if not isinstance(raw, str):
        return None
    upper = raw.strip().upper()
    return upper if upper in _VALID_LEVELS else None  # type: ignore[return-value]


def score_strategic_and_execution_risk(
    adapter: AIAdapter,
    context: dict[str, Any],
) -> RiskExplanationResult:
    if not context:
        return RiskExplanationResult(abstained=True, reason=ABSTAIN_TEXT + " No context provided.")

    prompt = _build_prompt(context)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return RiskExplanationResult(abstained=True, reason=f"AI unavailable: {exc}")

    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return RiskExplanationResult(abstained=True, reason=f"Could not parse AI response: {exc}")

    if not isinstance(parsed, dict):
        return RiskExplanationResult(abstained=True, reason="AI response was not a JSON object.")

    strategic_raw = parsed.get("strategic_risk") or {}
    execution_raw = parsed.get("execution_risk") or {}

    strategic_level = _validated_level(strategic_raw.get("score")) if isinstance(strategic_raw, dict) else None
    execution_level = _validated_level(execution_raw.get("score")) if isinstance(execution_raw, dict) else None

    if strategic_level is None or execution_level is None:
        return RiskExplanationResult(
            abstained=True,
            reason=(
                ABSTAIN_TEXT
                + " AI did not return a valid LOW/MEDIUM/HIGH score for both Strategic Risk and Execution Risk."
            ),
        )

    return RiskExplanationResult(
        abstained=False,
        strategic_risk=RiskCategoryScore(
            category="Strategic Risk",
            score=strategic_level,
            explanation=str(strategic_raw.get("explanation", "")),
            ai_assisted=True,
        ),
        execution_risk=RiskCategoryScore(
            category="Execution Risk",
            score=execution_level,
            explanation=str(execution_raw.get("explanation", "")),
            ai_assisted=True,
        ),
    )
