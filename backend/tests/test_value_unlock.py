"""Tests for backend/valuation/value_unlock.py (deterministic math) and
backend/ai/tasks/value_unlock_ideas.py (AI proposal path, requires
human approval before dollar figures)."""

from __future__ import annotations

import pytest

from backend.ai.testing import FakeAIAdapter
from backend.ai.tasks.value_unlock_ideas import ACTION_TYPE_CODES, VALID_ACTION_TYPES, propose_value_unlock_ideas
from backend.valuation.value_unlock import (
    compute_asset_sale,
    compute_buyback,
    compute_spin_off_or_separation,
)


def test_spin_off_uplift_hand_calculated():
    # Segment currently attributed EV = 500 (inside conglomerate discount).
    # Standalone: EBITDA 100 * standalone multiple 8x = 800.
    result = compute_spin_off_or_separation(
        action_type="spin_off",
        current_segment_value=500,
        standalone_metric_value=100,
        standalone_multiple=8,
    )
    assert result.potential_value == 800
    assert result.estimated_uplift == 300
    assert result.uplift_pct == pytest.approx(60.0)


def test_buyback_uplift_hand_calculated():
    # 1000 shares @ $50 = $50,000 equity value. $5,000 buyback repurchases
    # 100 shares -> 900 shares remaining, new equity value $45,000.
    # new price = 45000 / 900 = $50 -- unchanged if bought exactly at market
    # price with no re-rating (mechanically neutral), verifying the arithmetic
    # itself rather than assuming any accretion.
    result = compute_buyback(
        current_share_price=50,
        shares_outstanding=1000,
        current_equity_value=50000,
        buyback_amount=5000,
    )
    assert result.detail["shares_repurchased"] == 100
    assert result.detail["new_shares_outstanding"] == 900
    assert result.detail["new_equity_value"] == 45000
    assert result.potential_value == pytest.approx(50.0)
    assert result.estimated_uplift == pytest.approx(0.0)


def test_buyback_below_intrinsic_price_is_accretive():
    # Same setup, but current_equity_value reflects an undervalued stock:
    # market price $40 but "intrinsic" equity value used for the post-buyback
    # calc is $50,000 for 1000 shares (i.e. buying back at a discount to an
    # analyst's fair equity value is accretive on a per-share basis).
    result = compute_buyback(
        current_share_price=40,
        shares_outstanding=1000,
        current_equity_value=50000,
        buyback_amount=4000,  # repurchases 100 shares at $40 each
    )
    assert result.detail["shares_repurchased"] == 100
    assert result.detail["new_shares_outstanding"] == 900
    new_equity_value = 50000 - 4000
    expected_new_price = new_equity_value / 900
    assert result.potential_value == pytest.approx(expected_new_price)
    assert result.estimated_uplift == pytest.approx(expected_new_price - 40)
    assert result.estimated_uplift > 0


def test_asset_sale_uplift():
    result = compute_asset_sale(current_segment_value=200, sale_proceeds=350)
    assert result.potential_value == 350
    assert result.estimated_uplift == 150


def test_buyback_rejects_repurchasing_all_shares():
    with pytest.raises(ValueError):
        compute_buyback(
            current_share_price=10,
            shares_outstanding=100,
            current_equity_value=1000,
            buyback_amount=1500,
        )


# --------------------------------------------------------------------------
# AI proposal path -- requires approval before any dollar figure exists
# --------------------------------------------------------------------------


def test_ai_proposal_never_contains_dollar_figures():
    fake = FakeAIAdapter(
        fixed_text='{"ideas": [{"action_type": "spin_off", "target_segment": "Cloud", '
        '"rationale": "Trades at a discount inside the conglomerate."}]}'
    )
    result = propose_value_unlock_ideas(fake, sotp_breakdown={"Cloud": 500, "Retail": 300}, conglomerate_discount_pct=-15.0)

    assert result.abstained is False
    assert result.computes_dollar_figures is False
    assert result.requires_human_approval is True
    assert len(result.ideas) == 1
    assert result.ideas[0].action_type == "spin_off"
    # The result model has no field capable of holding a dollar uplift at all.
    assert not hasattr(result.ideas[0], "estimated_uplift")
    assert not hasattr(result.ideas[0], "potential_value")


def test_ai_proposal_rejects_invalid_action_type():
    fake = FakeAIAdapter(fixed_text='{"ideas": [{"action_type": "hostile_takeover", "rationale": "n/a"}]}')
    result = propose_value_unlock_ideas(fake, sotp_breakdown={"Cloud": 500})
    assert result.abstained is True


def test_action_type_codes_cover_all_valid_types():
    assert set(ACTION_TYPE_CODES.keys()) == set(VALID_ACTION_TYPES)
