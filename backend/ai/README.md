# backend/ai

AI reasoning layer. Produces assumption recommendations, peer/methodology
recommendations, devil's-advocate critiques, research summaries, and memo
prose only -- never writes authoritative data, never computes a valuation.
See docs/ai-governance.md for the full allowed/forbidden list.

- `adapter.py` -- the `AIAdapter` interface.
- `gemini_adapter.py` -- real `google-generativeai`-backed implementation
  (`GEMINI_API_KEY` env var); degrades to `AIUnavailableError`/
  `AIRateLimitError` instead of crashing or fabricating output.
- `testing.py` -- `FakeAIAdapter`, a deterministic test double.
- `errors.py` -- typed adapter errors.
- `validation.py` -- the number-traceability guardrail
  (`validate_ai_numbers`).
- `json_utils.py` -- shared JSON extraction from model text.
- `tasks/` -- the six structured task functions (see docs/ai-governance.md).
