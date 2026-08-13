"""Task: plain-English explanation of an already-computed reverse-valuation
result.

Purely a narrative wrapper -- ``backend.valuation.reverse_valuation`` has
already solved for the implied assumption and classified it
(CONSERVATIVE/REASONABLE/AGGRESSIVE/EXTREME) deterministically before this
task ever runs. This task never recomputes or overrides the classification
or the implied value; it only asks the AI to explain the already-final
numbers in prose, and every numeric token in that prose is checked against
the structured result via ``validate_ai_numbers`` before being trusted --
any AI output containing an untraceable number is discarded (ABSTAIN).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.validation import validate_ai_numbers

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."


class ReverseValuationExplanationResult(BaseModel):
    ai_origin: bool = True
    advisory_only: bool = True
    abstained: bool
    explanation: Optional[str] = None
    reason: Optional[str] = None


def _build_prompt(result: dict[str, Any]) -> str:
    return (
        "You are explaining an already-finalized, deterministic reverse-valuation result "
        "to an investor in plain English. Do NOT change, recompute, or second-guess the "
        "implied value or classification below -- both are final and were computed by "
        "non-AI code. Only explain what they mean in 2-4 sentences, using only the numbers "
        "given here (do not introduce new numbers).\n\n"
        f"Reverse valuation result: {result}\n\n"
        "Reply with plain text (no JSON, no markdown)."
    )


def explain_reverse_valuation(
    adapter: AIAdapter,
    reverse_valuation_result: dict[str, Any],
) -> ReverseValuationExplanationResult:
    if not reverse_valuation_result:
        return ReverseValuationExplanationResult(
            abstained=True,
            reason=ABSTAIN_TEXT + " No reverse valuation result provided.",
        )

    prompt = _build_prompt(reverse_valuation_result)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return ReverseValuationExplanationResult(abstained=True, reason=f"AI unavailable: {exc}")

    if not text or not text.strip():
        return ReverseValuationExplanationResult(abstained=True, reason=ABSTAIN_TEXT + " Empty AI response.")

    validation = validate_ai_numbers(text, reverse_valuation_result)
    if not validation.ok:
        return ReverseValuationExplanationResult(
            abstained=True,
            reason=f"AI explanation contained unverifiable numbers and was discarded: {validation.notes}",
        )

    return ReverseValuationExplanationResult(abstained=False, explanation=text.strip())
