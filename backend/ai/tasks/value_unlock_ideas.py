"""Task: propose WHICH structural actions might be worth modeling, given a
company's SOTP breakdown and conglomerate discount.

This task NEVER computes or states a dollar uplift figure -- it only
proposes action types + rationale (e.g. "spin off Segment X because it
trades at a discount inside the conglomerate"). Each proposed idea is shaped
so it can be handed to ``backend.governance.approval.propose()`` as an
AssumptionDecision-style artifact requiring human approval before
``backend.valuation.value_unlock`` is ever run on it -- reusing Phase 7's
existing propose()/AssumptionDecision approval pattern rather than inventing
a parallel one, since a proposed structural action is conceptually the same
shape as a proposed assumption: an AI recommendation with a rationale that a
human must approve/edit/reject before it feeds into deterministic math. The
"value" recorded on the AssumptionDecision row for one of these proposals is
not a dollar uplift; it is a stable numeric identifier for which action_type
was proposed (see ``ACTION_TYPE_CODES``) purely so it fits the existing
``ai_recommended_value: float`` column -- the actual dollar math only ever
happens in ``backend.valuation.value_unlock`` after human approval, driven
by human-supplied multiples/proceeds, never by anything in this module.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.json_utils import extract_json

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."

VALID_ACTION_TYPES = [
    "spin_off",
    "subsidiary_ipo",
    "asset_sale",
    "buyback",
    "debt_reduction",
    "special_dividend",
    "segment_separation",
]

# Stable mapping so a proposed action_type can be recorded as the
# AssumptionDecision.ai_recommended_value float column without smuggling a
# dollar figure into it -- purely a categorical id, documented here.
ACTION_TYPE_CODES: dict[str, float] = {name: float(i) for i, name in enumerate(VALID_ACTION_TYPES)}


class ValueUnlockIdea(BaseModel):
    action_type: str
    target_segment: Optional[str] = None
    rationale: str


class ValueUnlockIdeasResult(BaseModel):
    ai_origin: bool = True
    requires_human_approval: bool = True
    computes_dollar_figures: bool = False
    abstained: bool
    ideas: list[ValueUnlockIdea] = []
    reason: Optional[str] = None


def _build_prompt(sotp_breakdown: dict[str, Any], conglomerate_discount_pct: Optional[float]) -> str:
    return (
        "You are a corporate-structure strategist. Given the sum-of-the-parts (SOTP) "
        "breakdown below and the company's conglomerate discount/premium, propose which "
        "structural actions might be worth modeling in detail. Choose only from this fixed "
        f"list of action types: {VALID_ACTION_TYPES}. This is a RECOMMENDATION ONLY -- a "
        "human analyst must approve each idea before any dollar value is computed for it. "
        "Do NOT state or estimate any dollar uplift, price target, or valuation number "
        "yourself -- only propose the action type, which segment (if applicable), and a "
        "qualitative rationale.\n\n"
        f"SOTP breakdown: {sotp_breakdown}\n"
        f"Conglomerate discount/premium (%): {conglomerate_discount_pct}\n\n"
        'Reply as strict JSON: {"ideas": [{"action_type": "spin_off", "target_segment": '
        '"...", "rationale": "..."}, ...]}'
    )


def propose_value_unlock_ideas(
    adapter: AIAdapter,
    sotp_breakdown: dict[str, Any],
    conglomerate_discount_pct: Optional[float] = None,
) -> ValueUnlockIdeasResult:
    if not sotp_breakdown:
        return ValueUnlockIdeasResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " No SOTP breakdown provided.",
        )

    prompt = _build_prompt(sotp_breakdown, conglomerate_discount_pct)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return ValueUnlockIdeasResult(abstained=True, reason=f"AI unavailable: {exc}")

    try:
        parsed = extract_json(text)
    except ValueError as exc:
        return ValueUnlockIdeasResult(abstained=True, reason=f"Could not parse AI response: {exc}")

    if not isinstance(parsed, dict) or not parsed.get("ideas"):
        return ValueUnlockIdeasResult(abstained=True, reason=ABSTAIN_TEXT + " AI returned no ideas.")

    ideas: list[ValueUnlockIdea] = []
    for item in parsed["ideas"]:
        if not isinstance(item, dict):
            continue
        action_type = item.get("action_type")
        rationale = item.get("rationale")
        if action_type not in VALID_ACTION_TYPES or not rationale:
            continue
        ideas.append(
            ValueUnlockIdea(
                action_type=action_type,
                target_segment=item.get("target_segment"),
                rationale=str(rationale),
            )
        )

    if not ideas:
        return ValueUnlockIdeasResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " AI did not return any recognized (action_type in the fixed list) ideas.",
        )

    return ValueUnlockIdeasResult(abstained=False, ideas=ideas)
