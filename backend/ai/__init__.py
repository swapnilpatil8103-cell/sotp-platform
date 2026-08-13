"""
AI reasoning layer (Gemini-based).

This package will produce assumption recommendations, anomaly flags, and
source-grounded narrative drafts (Phase 6). It never writes authoritative
data directly -- outputs always land as pending AssumptionDecision rows for
human review, per docs/ai-governance.md. Phase 1 defines the adapter
interface only; no real API calls are made yet.
"""

from .adapter import AIAdapter, AIRecommendation

__all__ = ["AIAdapter", "AIRecommendation"]
