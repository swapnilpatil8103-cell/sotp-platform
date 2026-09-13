"""Unit tests for backend.valuation.beta_analysis -- peer beta
unlevering/relevering (Hamada equation)."""

from __future__ import annotations

import pytest

from backend.valuation.beta_analysis import (
    BetaAnalysisInput,
    PeerBetaInput,
    relever_beta,
    run_beta_analysis,
    unlever_beta,
)


def test_unlever_beta_hand_calculated():
    # beta_u = beta_l / (1 + (1-T)(D/E)) = 1.3 / (1 + 0.75*0.4) = 1.3/1.3 = 1.0
    beta_u = unlever_beta(1.3, 0.4, 0.25)
    assert beta_u == pytest.approx(1.0, abs=1e-9)


def test_relever_beta_hand_calculated():
    # beta_l = beta_u * (1 + (1-T)(D/E)) = 1.0 * (1 + 0.79*0.6) = 1.474
    beta_l = relever_beta(1.0, 0.6, 0.21)
    assert beta_l == pytest.approx(1.474, abs=1e-6)


def test_run_beta_analysis_single_peer_matches_hand_calculation():
    inputs = BetaAnalysisInput(
        peers=[PeerBetaInput(name="Peer A", levered_beta=1.3, debt_to_equity=0.4, tax_rate=0.25)],
        aggregation="median",
        target_debt_to_equity=0.6,
        target_tax_rate=0.21,
    )
    result = run_beta_analysis(inputs)
    assert result.unlevered_beta_aggregate == pytest.approx(1.0, abs=1e-9)
    assert result.relevered_beta == pytest.approx(1.474, abs=1e-6)
    assert len(result.used_peers) == 1
    assert result.skipped_peers == []


def test_run_beta_analysis_skips_peers_missing_capital_structure_data():
    inputs = BetaAnalysisInput(
        peers=[
            PeerBetaInput(name="Peer A", levered_beta=1.3, debt_to_equity=0.4, tax_rate=0.25),
            PeerBetaInput(name="Peer B (no D/E)", levered_beta=1.5, debt_to_equity=None, tax_rate=0.25),
            PeerBetaInput(name="Peer C (no tax)", levered_beta=1.2, debt_to_equity=0.3, tax_rate=None),
        ],
        aggregation="median",
        target_debt_to_equity=0.6,
        target_tax_rate=0.21,
    )
    result = run_beta_analysis(inputs)
    assert len(result.used_peers) == 1
    assert result.used_peers[0].name == "Peer A"
    assert len(result.skipped_peers) == 2
    skipped_names = {p.name for p in result.skipped_peers}
    assert skipped_names == {"Peer B (no D/E)", "Peer C (no tax)"}
    for p in result.skipped_peers:
        assert "Missing real capital-structure data" in p.reason


def test_run_beta_analysis_raises_when_no_usable_peer():
    inputs = BetaAnalysisInput(
        peers=[PeerBetaInput(name="Peer A", levered_beta=1.3, debt_to_equity=None, tax_rate=None)],
        aggregation="median",
        target_debt_to_equity=0.6,
        target_tax_rate=0.21,
    )
    with pytest.raises(ValueError):
        run_beta_analysis(inputs)


def test_run_beta_analysis_average_aggregation():
    inputs = BetaAnalysisInput(
        peers=[
            PeerBetaInput(name="A", levered_beta=1.3, debt_to_equity=0.4, tax_rate=0.25),  # u = 1.0
            PeerBetaInput(name="B", levered_beta=1.0, debt_to_equity=0.0, tax_rate=0.25),  # u = 1.0
        ],
        aggregation="average",
        target_debt_to_equity=0.0,
        target_tax_rate=0.25,
    )
    result = run_beta_analysis(inputs)
    assert result.unlevered_beta_aggregate == pytest.approx(1.0, abs=1e-9)
    # relever at D/E=0 -> unchanged
    assert result.relevered_beta == pytest.approx(1.0, abs=1e-9)
