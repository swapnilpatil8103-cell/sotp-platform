"""Shared helper for extracting a JSON object from an AI text response.

Models frequently wrap JSON in markdown code fences or add stray prose; this
extracts the first top-level JSON object/array found in the text. Raises
ValueError (never returns fabricated/partial structure) if none is found or
it doesn't parse -- callers should treat that as insufficient evidence to
proceed (ABSTAIN), not as license to guess.
"""

from __future__ import annotations

import json
import re


def extract_json(text: str) -> dict | list:
    if not text or not text.strip():
        raise ValueError("Empty AI response; cannot extract JSON.")

    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text.strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Fall back to the widest {...} or [...] span in the text.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        end = text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise ValueError(f"Could not extract valid JSON from AI response: {text!r}")
