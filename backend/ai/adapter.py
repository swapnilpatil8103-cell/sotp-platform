"""
AI adapter interface (Phase 1 stub).

Defines the contract a real Gemini-backed implementation will fulfill in
Phase 6. No network calls happen here yet -- this module exists so that
governance/audit wiring built in earlier phases doesn't need to change shape
when the real adapter lands.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AIRecommendation:
    """A single AI-proposed value, always accompanied by its rationale."""

    assumption_key: str
    recommended_value: float
    rationale: str
    confidence: Optional[float] = None
    model: Optional[str] = None


class AIAdapter(ABC):
    """
    Abstract interface for an AI reasoning provider.

    Implementations (e.g. GeminiAdapter, added in Phase 6) must only ever
    return recommendations -- they must never write to authoritative
    FinancialFact / AssumptionDecision.approved_value fields directly. Every
    call should be recorded via backend/audit/.
    """

    @abstractmethod
    def recommend_assumption(self, context: dict[str, Any]) -> AIRecommendation:
        """Propose a single assumption value given valuation context."""
        raise NotImplementedError

    @abstractmethod
    def draft_narrative(self, context: dict[str, Any]) -> str:
        """Draft narrative research text grounded in provided source data."""
        raise NotImplementedError

    @abstractmethod
    def generate_text(self, prompt: str) -> str:
        """
        Generic text-generation call used by backend/ai/tasks/*.

        Implementations must raise backend.ai.errors.AIUnavailableError if
        the provider is unreachable/unconfigured, and
        backend.ai.errors.AIRateLimitError on rate-limit/quota rejection.
        They must never crash the caller and never fabricate a response --
        no key/no connectivity means "raise AIUnavailableError", not "return
        a made-up string".
        """
        raise NotImplementedError
