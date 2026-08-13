"""Task: summarize a company/filing from real, already-extracted data.

The prompt includes ONLY facts already sourced from SEC/market data (passed
in by the caller as ``facts``/``segments`` -- never invented here). The
output is validated with backend/ai/validation.py to catch any number the
model introduces that isn't traceable back to the input.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from backend.ai.adapter import AIAdapter
from backend.ai.errors import AIError
from backend.ai.validation import validate_ai_numbers

ABSTAIN_TEXT = "Insufficient evidence to make a reliable recommendation."


class ResearchSummaryResult(BaseModel):
    ticker: str
    ai_origin: bool = True
    abstained: bool
    summary_text: Optional[str] = None
    validation_ok: Optional[bool] = None
    validation_notes: Optional[str] = None
    reason: Optional[str] = None


def _build_prompt(ticker: str, facts: dict[str, Any], segments: list[dict[str, Any]]) -> str:
    return (
        "You are a financial research assistant. Write a concise, neutral summary "
        f"of {ticker} using ONLY the structured data below. Do not invent, estimate, "
        "or restate any number that is not explicitly present in this data. If a "
        "figure is not present, describe it qualitatively instead of guessing a "
        "value.\n\n"
        f"Company-level facts: {facts}\n\n"
        f"Segments: {segments}\n\n"
        "Reply with plain prose (2-5 sentences), no headers."
    )


def generate_research_summary(
    adapter: AIAdapter,
    ticker: str,
    facts: dict[str, Any],
    segments: Optional[list[dict[str, Any]]] = None,
) -> ResearchSummaryResult:
    """Summarize ``facts``/``segments`` (already-sourced, real data) for
    ``ticker``. Returns an ABSTAIN result if there is no usable input data or
    if the AI is unavailable/its output fails the number-traceability
    guardrail."""
    segments = segments or []

    if not facts and not segments:
        return ResearchSummaryResult(
            ticker=ticker,
            abstained=True,
            reason=ABSTAIN_TEXT + " No sourced facts or segments were provided.",
        )

    prompt = _build_prompt(ticker, facts, segments)

    try:
        text = adapter.generate_text(prompt)
    except AIError as exc:
        return ResearchSummaryResult(
            ticker=ticker,
            abstained=True,
            reason=f"AI unavailable: {exc}",
        )

    validation = validate_ai_numbers(text, {"facts": facts, "segments": segments})
    if not validation.ok:
        return ResearchSummaryResult(
            ticker=ticker,
            abstained=True,
            validation_ok=False,
            validation_notes=validation.notes,
            reason="AI summary introduced numbers not traceable to input data; discarded.",
        )

    return ResearchSummaryResult(
        ticker=ticker,
        abstained=False,
        summary_text=text.strip(),
        validation_ok=True,
        validation_notes=validation.notes,
    )
