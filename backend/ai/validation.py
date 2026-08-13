"""Guardrail: verify numbers in AI output are traceable to the input data.

This is the concrete enforcement of "AI cannot fabricate financial facts"
(docs/ai-governance.md rule 1). It is a heuristic, not a proof:

LIMITATIONS (documented honestly):
- It extracts numeric tokens from AI text via regex and checks each one
  against a flattened set of numbers pulled from the structured input
  (plus simple derived transforms: rounding to 0/1/2 decimals, and
  percentage forms of the same value, e.g. 0.08 vs 8 vs 8.0 vs 8%).
- It cannot detect a fabricated *qualitative* claim ("revenue grew due to
  strong iPhone sales") that happens to use no numbers, nor can it verify
  that a real number was used in a truthful *context* (e.g. citing a real
  input number but misattributing which segment it belongs to).
- It will also flag legitimate small integers that commonly appear in
  prose without being data points (list markers, years like "2024", section
  numbers) as unmatched -- callers should tune ``ignore_below`` /
  ``allow_years`` for their use case rather than treat every flag as
  malicious.
- It is intentionally conservative in one specific direction: matches use a
  relative tolerance, so this does NOT catch small numeric drift (e.g. an
  AI restating $101.9B as $102B) -- only numbers with no plausible
  relationship to any input value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*%?")

_REL_TOL = 0.005  # 0.5% relative tolerance for rounding / formatting drift


@dataclass
class ValidationResult:
    ok: bool
    checked_numbers: list[float] = field(default_factory=list)
    unmatched_numbers: list[float] = field(default_factory=list)
    notes: str = ""


def _flatten_numbers(data: Any) -> set[float]:
    out: set[float] = set()

    def _walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, (int, float)):
            out.add(float(v))
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                _walk(x)
        elif hasattr(v, "__dict__"):
            _walk(vars(v))

    _walk(data)
    return out


def _extract_numbers(text: str) -> list[float]:
    numbers: list[float] = []
    for raw in _NUMBER_RE.findall(text):
        token = raw.strip()
        if not token or token in {"-", "."}:
            continue
        is_pct = token.endswith("%")
        token = token.rstrip("%").replace(",", "")
        if token in {"-", "", "."}:
            continue
        try:
            value = float(token)
        except ValueError:
            continue
        numbers.append(value / 100.0 if is_pct else value)
        if is_pct:
            numbers.append(value)  # also keep the raw "8" form for percent-as-fraction inputs
    return numbers


def _matches_any(candidate: float, allowed: set[float]) -> bool:
    for a in allowed:
        if a == 0 and candidate == 0:
            return True
        if a == 0:
            continue
        if abs(candidate - a) <= _REL_TOL * abs(a):
            return True
        # percentage-fraction equivalence: 0.08 vs 8
        if abs(candidate - a * 100) <= _REL_TOL * abs(a * 100):
            return True
        if abs(candidate - a / 100) <= _REL_TOL * max(abs(a / 100), 1e-9):
            return True
        # simple rounding transforms (0/1/2 decimal places)
        for ndigits in (0, 1, 2):
            if round(a, ndigits) == candidate:
                return True
    return False


def validate_ai_numbers(
    ai_text: str,
    input_data: Any,
    ignore_below: float = 4.0,
    allow_years: bool = True,
) -> ValidationResult:
    """Check that every numeric token in ``ai_text`` is traceable to a number
    present in ``input_data`` (or a simple derived transform of one).

    ``ignore_below``: numbers with absolute value below this are skipped by
    default (small counts like "3 segments", ordinal-ish digits) unless they
    round-trip is still attempted -- set to 0 to check everything.
    ``allow_years``: numbers that look like a plausible calendar year
    (1990-2099) are never flagged, since they're structural, not financial
    facts.
    """
    allowed = _flatten_numbers(input_data)
    checked: list[float] = []
    unmatched: list[float] = []

    for num in _extract_numbers(ai_text):
        if allow_years and 1990.0 <= num <= 2099.0 and num == int(num):
            continue
        if abs(num) < ignore_below:
            continue
        checked.append(num)
        if not _matches_any(num, allowed):
            unmatched.append(num)

    ok = len(unmatched) == 0
    notes = (
        "All numeric tokens traced to input data."
        if ok
        else f"{len(unmatched)} numeric token(s) not traceable to input data: {unmatched}"
    )
    return ValidationResult(ok=ok, checked_numbers=checked, unmatched_numbers=unmatched, notes=notes)
