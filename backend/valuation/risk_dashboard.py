"""Risk dashboard: six-category risk scoring.

Four categories are purely rule-based (Data, Model, Forecast, Market Risk),
scored from real inputs already produced elsewhere in the system. Two
categories (Strategic Risk, Execution Risk) are less quantifiable and are
AI-assisted: the AI proposes a score, but it is constrained to the
LOW/MEDIUM/HIGH enum (never free text) by ``score_strategic_and_execution_risk``
in ``backend/ai/tasks/risk_explanation.py`` -- this module only defines the
enum-validation helper and composes the final six-category dashboard; the
actual AI call lives in the ai/tasks module per the AI-boundary convention
used throughout this codebase (backend/valuation/ never calls backend/ai/).

Documented thresholds (all fixed here, not invented ad hoc downstream):

- Data Risk: from segment/financial-fact coverage % (Phase 2/3's
  ``segment_coverage`` scoring, 0-100 scale).
    coverage >= 85  -> LOW
    60 <= coverage < 85 -> MEDIUM
    coverage < 60   -> HIGH
- Model Risk: from (a) how many valuation methodologies were eligible vs.
  actually used (Phase 5's business_classifier eligibility list), and (b)
  sensitivity spread (max-min implied price / base implied price, from
  Phase 5's sensitivity matrix).
    methodologies_used >= 2 AND sensitivity_spread_pct <= 20 -> LOW
    methodologies_used == 1 OR 20 < sensitivity_spread_pct <= 40 -> MEDIUM
    methodologies_used == 0 OR sensitivity_spread_pct > 40 -> HIGH
  (methodologies_used == 0 forces HIGH regardless of spread.)
- Forecast Risk: reuses reverse_valuation's CONSERVATIVE/REASONABLE/
  AGGRESSIVE/EXTREME classification of the key growth assumption.
    REASONABLE or CONSERVATIVE -> LOW
    AGGRESSIVE -> MEDIUM
    EXTREME -> HIGH
- Market Risk: from beta and 1y realized volatility (Phase 4 market data).
    beta <= 1.1 AND volatility_pct <= 30 -> LOW
    beta <= 1.5 AND volatility_pct <= 50 -> MEDIUM
    otherwise -> HIGH
  (if beta is None, classified from volatility_pct alone using the same
  cutoffs; if both are None, HIGH -- absence of market risk data is itself a
  risk, not assumed benign.)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from backend.schemas.valuation import RiskCategoryScore, RiskLevel

__all__ = [
    "RiskLevel",
    "RiskCategoryScore",
    "RiskDashboardResult",
    "score_data_risk",
    "score_model_risk",
    "score_forecast_risk",
    "score_market_risk",
    "build_risk_dashboard",
]


class RiskDashboardResult(BaseModel):
    data_risk: RiskCategoryScore
    model_risk: RiskCategoryScore
    forecast_risk: RiskCategoryScore
    market_risk: RiskCategoryScore
    strategic_risk: RiskCategoryScore
    execution_risk: RiskCategoryScore

    @property
    def categories(self) -> list[RiskCategoryScore]:
        return [
            self.data_risk,
            self.model_risk,
            self.forecast_risk,
            self.market_risk,
            self.strategic_risk,
            self.execution_risk,
        ]


def score_data_risk(coverage_pct: float) -> RiskCategoryScore:
    if coverage_pct >= 85:
        score: RiskLevel = "LOW"
    elif coverage_pct >= 60:
        score = "MEDIUM"
    else:
        score = "HIGH"
    return RiskCategoryScore(
        category="Data Risk",
        score=score,
        explanation=f"Segment/financial-fact coverage is {coverage_pct:.1f}%.",
    )


def score_model_risk(methodologies_used: int, sensitivity_spread_pct: float) -> RiskCategoryScore:
    if methodologies_used == 0:
        score: RiskLevel = "HIGH"
    elif methodologies_used >= 2 and sensitivity_spread_pct <= 20:
        score = "LOW"
    elif sensitivity_spread_pct > 40:
        score = "HIGH"
    elif methodologies_used == 1 or sensitivity_spread_pct > 20:
        score = "MEDIUM"
    else:
        score = "LOW"
    return RiskCategoryScore(
        category="Model Risk",
        score=score,
        explanation=(
            f"{methodologies_used} methodology(ies) used; sensitivity spread "
            f"{sensitivity_spread_pct:.1f}% of base implied price."
        ),
    )


def score_forecast_risk(classification: Literal["CONSERVATIVE", "REASONABLE", "AGGRESSIVE", "EXTREME"]) -> RiskCategoryScore:
    mapping: dict[str, RiskLevel] = {
        "CONSERVATIVE": "LOW",
        "REASONABLE": "LOW",
        "AGGRESSIVE": "MEDIUM",
        "EXTREME": "HIGH",
    }
    return RiskCategoryScore(
        category="Forecast Risk",
        score=mapping[classification],
        explanation=f"Key growth assumption classified as {classification} vs. historical/peer range.",
    )


def score_market_risk(beta: Optional[float], volatility_pct: Optional[float]) -> RiskCategoryScore:
    if beta is None and volatility_pct is None:
        return RiskCategoryScore(
            category="Market Risk",
            score="HIGH",
            explanation="No beta or volatility data available; absence of market-risk data is treated as HIGH risk, not assumed benign.",
        )

    def _level_for(b: Optional[float], v: Optional[float]) -> RiskLevel:
        b_ok_low = b is None or b <= 1.1
        v_ok_low = v is None or v <= 30
        b_ok_med = b is None or b <= 1.5
        v_ok_med = v is None or v <= 50
        if b_ok_low and v_ok_low:
            return "LOW"
        if b_ok_med and v_ok_med:
            return "MEDIUM"
        return "HIGH"

    score = _level_for(beta, volatility_pct)
    return RiskCategoryScore(
        category="Market Risk",
        score=score,
        explanation=f"beta={beta}, 1y volatility={volatility_pct}%.",
    )


def build_risk_dashboard(
    *,
    coverage_pct: float,
    methodologies_used: int,
    sensitivity_spread_pct: float,
    forecast_classification: Literal["CONSERVATIVE", "REASONABLE", "AGGRESSIVE", "EXTREME"],
    beta: Optional[float],
    volatility_pct: Optional[float],
    strategic_risk: RiskCategoryScore,
    execution_risk: RiskCategoryScore,
) -> RiskDashboardResult:
    """Compose the four rule-based scores with the two AI-assisted scores
    (already computed and enum-validated by the caller via
    ``backend.ai.tasks.risk_explanation.score_strategic_and_execution_risk``)
    into the final six-category dashboard."""
    if strategic_risk.category != "Strategic Risk" or not strategic_risk.ai_assisted:
        raise ValueError("strategic_risk must be an ai_assisted RiskCategoryScore with category='Strategic Risk'")
    if execution_risk.category != "Execution Risk" or not execution_risk.ai_assisted:
        raise ValueError("execution_risk must be an ai_assisted RiskCategoryScore with category='Execution Risk'")

    return RiskDashboardResult(
        data_risk=score_data_risk(coverage_pct),
        model_risk=score_model_risk(methodologies_used, sensitivity_spread_pct),
        forecast_risk=score_forecast_risk(forecast_classification),
        market_risk=score_market_risk(beta, volatility_pct),
        strategic_risk=strategic_risk,
        execution_risk=execution_risk,
    )
