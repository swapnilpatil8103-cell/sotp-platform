"""Task: generate investment memo section prose from real, computed data.

Every number appearing in the generated text must trace back to a field in
``memo_data`` (already-computed valuation/segment/risk data) -- enforced via
backend/ai/validation.py. This task never computes or estimates a new
number; it only writes prose describing numbers it was given.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.validation import validate_ai_numbers

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."


class MemoSectionResult(BaseModel):
    ai_origin: bool = True
    section: str
    abstained: bool
    memo_text: Optional[str] = None
    validation_ok: Optional[bool] = None
    validation_notes: Optional[str] = None
    reason: Optional[str] = None


def _build_prompt(section: str, memo_data: dict[str, Any]) -> str:
    return (
        f"You are an investment memo writer. Draft the '{section}' section of an "
        "investment memo using ONLY the structured data below. Every number you "
        "write must come directly from this data -- never invent, estimate, or "
        "round to a materially different figure. If key data is missing, note the "
        "gap in prose rather than filling it in.\n\n"
        f"Data: {memo_data}\n\n"
        "Reply with plain prose suitable for a memo section, no headers."
    )


def generate_memo_section(
    adapter: AIAdapter,
    section: str,
    memo_data: dict[str, Any],
) -> MemoSectionResult:
    if not memo_data:
        return MemoSectionResult(
            section=section,
            abstained=True,
            reason=ABSTAIN_TEXT + " No computed valuation/segment/risk data provided.",
        )

    prompt = _build_prompt(section, memo_data)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return MemoSectionResult(section=section, abstained=True, reason=f"AI unavailable: {exc}")

    validation = validate_ai_numbers(text, memo_data)
    if not validation.ok:
        return MemoSectionResult(
            section=section,
            abstained=True,
            validation_ok=False,
            validation_notes=validation.notes,
            reason="AI memo text introduced numbers not traceable to input data; discarded.",
        )

    return MemoSectionResult(
        section=section,
        abstained=False,
        memo_text=text.strip(),
        validation_ok=True,
        validation_notes=validation.notes,
    )
