"""Task: actively challenge a completed valuation output.

Purely advisory -- this task never modifies the valuation it is given. It
reads a completed valuation result (already computed by backend/valuation/)
and produces a structured critique across seven risk categories. The
challenged fair value range is an AI-proposed range for discussion, not a
replacement valuation.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.json_utils import extract_json

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."

RISK_CATEGORIES = [
    "Data Risk",
    "Model Risk",
    "Forecast Risk",
    "Multiple Risk",
    "Market Risk",
    "Strategic Risk",
    "Execution Risk",
]


class RiskCategoryChallenge(BaseModel):
    category: str
    challenge: str


class DevilsAdvocateResult(BaseModel):
    ai_origin: bool = True
    advisory_only: bool = True
    abstained: bool
    risk_challenges: list[RiskCategoryChallenge] = []
    strongest_reason_too_high: Optional[str] = None
    strongest_reason_too_low: Optional[str] = None
    most_sensitive_assumption: Optional[str] = None
    weakest_data_input: Optional[str] = None
    challenged_fair_value_low: Optional[float] = None
    challenged_fair_value_high: Optional[float] = None
    reason: Optional[str] = None


def _build_prompt(valuation_output: dict[str, Any]) -> str:
    return (
        "You are a skeptical, independent 'devil's advocate' reviewer of an already-"
        "completed valuation. You must NOT change the valuation itself -- your job is "
        f"purely to challenge it across these risk categories: {RISK_CATEGORIES}. Base "
        "every critique on the structured valuation data below; do not invent facts "
        "not present in it.\n\n"
        f"Valuation output: {valuation_output}\n\n"
        'Reply as strict JSON: {"risk_challenges": [{"category": "Data Risk", '
        '"challenge": "..."}, ...], "strongest_reason_too_high": "...", '
        '"strongest_reason_too_low": "...", "most_sensitive_assumption": "...", '
        '"weakest_data_input": "...", "challenged_fair_value_low": 0, '
        '"challenged_fair_value_high": 0}'
    )


def run_devils_advocate(
    adapter: AIAdapter,
    valuation_output: dict[str, Any],
) -> DevilsAdvocateResult:
    if not valuation_output:
        return DevilsAdvocateResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " No completed valuation output provided to critique.",
        )

    prompt = _build_prompt(valuation_output)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return DevilsAdvocateResult(abstained=True, reason=f"AI unavailable: {exc}")

    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return DevilsAdvocateResult(abstained=True, reason=f"Could not parse AI response: {exc}")

    if not isinstance(parsed, dict):
        return DevilsAdvocateResult(abstained=True, reason="AI response was not a JSON object.")

    raw_challenges = parsed.get("risk_challenges", [])
    challenges: list[RiskCategoryChallenge] = []
    for c in raw_challenges:
        if isinstance(c, dict) and c.get("category") in RISK_CATEGORIES and c.get("challenge"):
            challenges.append(RiskCategoryChallenge(category=c["category"], challenge=str(c["challenge"])))

    if not challenges:
        return DevilsAdvocateResult(
            abstained=True, reason=ABSTAIN_TEXT + " AI did not return any recognized risk-category challenges."
        )

    def _as_float(key: str) -> Optional[float]:
        v = parsed.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return DevilsAdvocateResult(
        abstained=False,
        risk_challenges=challenges,
        strongest_reason_too_high=parsed.get("strongest_reason_too_high"),
        strongest_reason_too_low=parsed.get("strongest_reason_too_low"),
        most_sensitive_assumption=parsed.get("most_sensitive_assumption"),
        weakest_data_input=parsed.get("weakest_data_input"),
        challenged_fair_value_low=_as_float("challenged_fair_value_low"),
        challenged_fair_value_high=_as_float("challenged_fair_value_high"),
    )
