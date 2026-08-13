"""Unit tests for backend.valuation.comps -- verifies per-peer multiple
computation, percentile stats (against numpy.percentile directly on a known
peer set), and implied valuation from a chosen multiple."""

from __future__ import annotations

import numpy as np
import pytest

from backend.schemas.valuation import CompPeer, CompsInput
from backend.valuation.comps import compute_peer_multiples, run_comps


def _peers() -> list[CompPeer]:
    return [
        CompPeer(name="A", revenue=1000, ebitda=300, ebit=250, net_income=150, market_cap=2500, debt=200, cash=100, book_value_equity=800),
        CompPeer(name="B", revenue=1200, ebitda=400, ebit=350, net_income=220, market_cap=3200, debt=300, cash=150, book_value_equity=900),
        CompPeer(name="C", revenue=800, ebitda=200, ebit=150, net_income=90, market_cap=1500, debt=100, cash=50, book_value_equity=500),
    ]


def test_peer_ev_and_multiples_hand_calculated():
    peer_a = _peers()[0]
    pm = compute_peer_multiples(peer_a)

    # EV = market_cap + debt - cash = 2500 + 200 - 100 = 2600
    assert peer_a.enterprise_value == pytest.approx(2600.0)
    assert pm.ev_to_revenue == pytest.approx(2600 / 1000)
    assert pm.ev_to_ebitda == pytest.approx(2600 / 300)
    assert pm.ev_to_ebit == pytest.approx(2600 / 250)
    assert pm.price_to_earnings == pytest.approx(2500 / 150)
    assert pm.price_to_book == pytest.approx(2500 / 800)


def test_percentile_stats_match_numpy_directly():
    peers = _peers()
    evs = [p.enterprise_value for p in peers]  # [2600, 3350, 1550]
    ebitdas = [p.ebitda for p in peers]  # [300, 400, 200]
    ev_to_ebitda_values = [ev / e for ev, e in zip(evs, ebitdas)]

    expected_median = float(np.median(ev_to_ebitda_values))
    expected_p25 = float(np.percentile(ev_to_ebitda_values, 25))
    expected_p75 = float(np.percentile(ev_to_ebitda_values, 75))
    expected_min = float(np.min(ev_to_ebitda_values))
    expected_max = float(np.max(ev_to_ebitda_values))

    inputs = CompsInput(
        peers=peers,
        target_name="Target",
        target_revenue=900,
        target_ebitda=350,
        target_ebit=300,
        target_net_income=180,
        target_book_value_equity=700,
        target_debt=150,
        target_cash=80,
        target_diluted_shares_outstanding=100,
        chosen_multiple_metric="ev_to_ebitda",
        chosen_multiple_value=expected_median,
    )
    result = run_comps(inputs)
    stats = {s.metric: s for s in result.stats}
    ev_ebitda_stats = stats["ev_to_ebitda"]

    assert ev_ebitda_stats.median == pytest.approx(expected_median)
    assert ev_ebitda_stats.p25 == pytest.approx(expected_p25)
    assert ev_ebitda_stats.p75 == pytest.approx(expected_p75)
    assert ev_ebitda_stats.min == pytest.approx(expected_min)
    assert ev_ebitda_stats.max == pytest.approx(expected_max)


def test_implied_valuation_from_ev_based_multiple():
    peers = _peers()
    median_ev_ebitda = float(np.median([p.enterprise_value / p.ebitda for p in peers]))

    inputs = CompsInput(
        peers=peers,
        target_name="Target",
        target_revenue=900,
        target_ebitda=350,
        target_ebit=300,
        target_net_income=180,
        target_book_value_equity=700,
        target_debt=150,
        target_cash=80,
        target_diluted_shares_outstanding=100,
        chosen_multiple_metric="ev_to_ebitda",
        chosen_multiple_value=median_ev_ebitda,
    )
    result = run_comps(inputs)

    expected_ev = 350 * median_ev_ebitda
    expected_equity = expected_ev - 150 + 80
    expected_price = expected_equity / 100

    assert result.implied_enterprise_value == pytest.approx(expected_ev)
    assert result.implied_equity_value == pytest.approx(expected_equity)
    assert result.implied_price_per_share == pytest.approx(expected_price)


def test_implied_valuation_from_equity_based_multiple_pe():
    peers = _peers()
    median_pe = float(np.median([p.market_cap / p.net_income for p in peers]))

    inputs = CompsInput(
        peers=peers,
        target_name="Target",
        target_revenue=900,
        target_ebitda=350,
        target_ebit=300,
        target_net_income=180,
        target_book_value_equity=700,
        target_debt=150,
        target_cash=80,
        target_diluted_shares_outstanding=100,
        chosen_multiple_metric="price_to_earnings",
        chosen_multiple_value=median_pe,
    )
    result = run_comps(inputs)

    expected_equity = 180 * median_pe
    assert result.implied_enterprise_value is None
    assert result.implied_equity_value == pytest.approx(expected_equity)
    assert result.implied_price_per_share == pytest.approx(expected_equity / 100)
