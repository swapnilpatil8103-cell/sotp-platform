"""Task: recommend which eligible valuation methodologies to emphasize.

The eligible-methodology list itself is always the deterministic output of
backend/data/business_classifier.py -- this task can only rank/explain
methods already on that list, never introduce one that isn't. Any AI-named
methodology outside the eligible list is dropped, not trusted.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.json_utils import extract_json

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."


class MethodologyRecommendationResult(BaseModel):
    ai_origin: bool = True
    abstained: bool
    ranked_methodologies: list[str] = []
    rationale_by_method: dict[str, str] = {}
    dropped_out_of_scope: list[str] = []
    reason: Optional[str] = None


def _build_prompt(category: str, eligible_methodologies: list[str], segment_coverage: dict[str, Any]) -> str:
    return (
        "You are a valuation methodology advisor. A deterministic rules engine has "
        f"already classified this company's business type as '{category}' and "
        f"restricted the eligible valuation methodologies to exactly this list: "
        f"{eligible_methodologies}. You must NOT propose any methodology outside "
        "this list. Given the segment data coverage below, rank the eligible "
        "methodologies from most to least appropriate to emphasize, and give a "
        "one-sentence rationale per method.\n\n"
        f"Segment coverage: {segment_coverage}\n\n"
        'Reply as strict JSON: {"ranked": ["METHOD1", "METHOD2", ...], '
        '"rationale": {"METHOD1": "...", ...}}'
    )


def recommend_methodology(
    adapter: AIAdapter,
    category: str,
    eligible_methodologies: list[str],
    segment_coverage: Optional[dict[str, Any]] = None,
) -> MethodologyRecommendationResult:
    segment_coverage = segment_coverage or {}

    if not eligible_methodologies:
        return MethodologyRecommendationResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " No eligible methodologies were provided by the classifier.",
        )

    prompt = _build_prompt(category, eligible_methodologies, segment_coverage)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return MethodologyRecommendationResult(
            abstained=True,
            reason=f"AI unavailable: {exc}",
        )

    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return MethodologyRecommendationResult(
            abstained=True,
            reason=f"Could not parse AI response: {exc}",
        )

    if not isinstance(parsed, dict):
        return MethodologyRecommendationResult(abstained=True, reason="AI response was not a JSON object.")

    raw_ranked = parsed.get("ranked", [])
    raw_rationale = parsed.get("rationale", {})

    eligible_set = set(eligible_methodologies)
    ranked = [m for m in raw_ranked if m in eligible_set]
    dropped = [m for m in raw_ranked if m not in eligible_set]
    rationale = {k: v for k, v in raw_rationale.items() if k in eligible_set}

    if not ranked:
        return MethodologyRecommendationResult(
            abstained=True,
            dropped_out_of_scope=dropped,
            reason=ABSTAIN_TEXT + " AI did not return any in-scope methodology.",
        )

    return MethodologyRecommendationResult(
        abstained=False,
        ranked_methodologies=ranked,
        rationale_by_method=rationale,
        dropped_out_of_scope=dropped,
    )
