"""Task: recommend candidate peer tickers for comps.

This is explicitly a RECOMMENDATION requiring human approval before any
ticker is used in backend/valuation/comps.py -- nothing here writes to a
CompsInput or persists a peer set. Phase 7 governance wires the approval
step; this task only produces the proposal.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.json_utils import extract_json

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."


class PeerCandidate(BaseModel):
    ticker: str
    rationale: str


class PeerRecommendationResult(BaseModel):
    ai_origin: bool = True
    requires_human_approval: bool = True
    abstained: bool
    candidates: list[PeerCandidate] = []
    reason: Optional[str] = None


def _build_prompt(ticker: str, business_category: str, profile: dict[str, Any]) -> str:
    return (
        "You are a comparable-companies research assistant. Given the target company "
        f"below, propose 3-6 candidate peer tickers for relative valuation (comps). "
        "This is a RECOMMENDATION ONLY -- it will be reviewed and approved or "
        "rejected by a human analyst before use; do not claim certainty.\n\n"
        f"Target ticker: {ticker}\n"
        f"Business category: {business_category}\n"
        f"Financial profile: {profile}\n\n"
        'Reply as strict JSON: {"candidates": [{"ticker": "XYZ", "rationale": "..."}, ...]}'
    )


def recommend_peers(
    adapter: AIAdapter,
    ticker: str,
    business_category: str,
    profile: Optional[dict[str, Any]] = None,
) -> PeerRecommendationResult:
    profile = profile or {}

    if not business_category:
        return PeerRecommendationResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " No business classification available to ground peer selection.",
        )

    prompt = _build_prompt(ticker, business_category, profile)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return PeerRecommendationResult(abstained=True, reason=f"AI unavailable: {exc}")

    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return PeerRecommendationResult(abstained=True, reason=f"Could not parse AI response: {exc}")

    if not isinstance(parsed, dict) or not parsed.get("candidates"):
        return PeerRecommendationResult(abstained=True, reason=ABSTAIN_TEXT + " AI returned no candidates.")

    candidates: list[PeerCandidate] = []
    for c in parsed["candidates"]:
        if isinstance(c, dict) and c.get("ticker"):
            candidates.append(
                PeerCandidate(ticker=str(c["ticker"]).upper(), rationale=str(c.get("rationale", "")))
            )

    if not candidates:
        return PeerRecommendationResult(abstained=True, reason=ABSTAIN_TEXT + " No valid candidates parsed.")

    return PeerRecommendationResult(abstained=False, candidates=candidates)
