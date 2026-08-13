"""Typed AI adapter errors.

All AI adapters must raise only these (never let raw SDK exceptions escape)
so callers can degrade gracefully -- never crash, never silently substitute
fabricated content. See docs/ai-governance.md.
"""

from __future__ import annotations


class AIError(Exception):
    """Base class for all AI adapter errors."""


class AIUnavailableError(AIError):
    """The AI provider could not be reached or is not configured (e.g. no
    API key set). Callers must treat this as an ABSTAIN condition, not a
    fatal error."""


class AIRateLimitError(AIError):
    """The AI provider rejected the call due to rate limiting/quota."""


class AIResponseParseError(AIError):
    """The AI provider returned a response that could not be parsed into
    the expected structured shape."""
