"""Modelling-rigor additions: incremental-revenue NWC, DCF/SOTP/comps warnings, IQR outlier trimming.

Expected figures are worked out by hand in the comments (independent of the engine).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.api.main import app
from backend.schemas.valuation import CompPeer, CompsInput, DcfInput, SotpInput, SotpSegmentInput
from backend.tests.fixtures.auth import auth_headers
from backend.valuation.comps import analyse_multiple, compute_multiple_stats, compute_peer_multiples, run_comps
from backend.valuation.dcf import run_dcf
from backend.valuation.sotp import run_sotp

client = TestClient(app, raise_server_exceptions=False, headers=auth_headers())


def codes(result) -> set[str]:
    return {w.code for w in result.warnings}


# ==========================================================================
# DCF: NWC from the change in revenue
# ==========================================================================


def dcf_kwargs(**overrides) -> dict:
    """base 1000, +10%/yr for 2 years, 20% EBIT margin, 25% tax, D&A 5%, CapEx 6%, WACC 10%, g 3%.

    Revenue 1100, 1210. NOPAT 165, 181.5. D&A 55, 60.5. CapEx 66, 72.6.
    """
    kw = dict(
        base_revenue=1000.0,
        revenue_growth_rates=[0.10, 0.10],
        ebit_margins=[0.20, 0.20],
        tax_rate=0.25,
        da_pct_of_revenue=[0.05, 0.05],
        capex_pct_of_revenue=[0.06, 0.06],
        nwc_pct_of_incremental_revenue=0.15,
        wacc=0.10,
        terminal_growth_rate=0.03,
        total_debt=200.0,
        cash_and_equivalents=100.0,
        diluted_shares_outstanding=100.0,
    )
    kw.update(overrides)
    return {k: v for k, v in kw.items() if v is not None}


def test_nwc_change_is_a_fraction_of_the_increase_in_revenue():
    r = run_dcf(DcfInput(**dcf_kwargs()))

    y1, y2 = r.projections
    # Year 1 increase = 1100 - 1000 = 100 -> 15; year 2 increase = 1210 - 1100 = 110 -> 16.5.
    assert y1.nwc_change == pytest.approx(15.0)
    assert y2.nwc_change == pytest.approx(16.5)
    # FCFF = NOPAT + D&A - CapEx - NWC: 165 + 55 - 66 - 15 = 139; 181.5 + 60.5 - 72.6 - 16.5 = 152.9.
    assert y1.fcff == pytest.approx(139.0)
    assert y2.fcff == pytest.approx(152.9)


def test_year_one_increase_is_measured_against_base_revenue():
    r = run_dcf(DcfInput(**dcf_kwargs(base_revenue=2000.0, revenue_growth_rates=[0.0, 0.5], ebit_margins=[0.2, 0.2],
                                      da_pct_of_revenue=[0.05, 0.05], capex_pct_of_revenue=[0.06, 0.06])))
    # No growth in year 1 -> no working-capital investment; year 2: revenue 2000 -> 3000, 15% of 1000 = 150.
    assert r.projections[0].nwc_change == pytest.approx(0.0)
    assert r.projections[1].nwc_change == pytest.approx(150.0)


def test_a_shrinking_year_releases_working_capital():
    r = run_dcf(DcfInput(**dcf_kwargs(revenue_growth_rates=[-0.10, 0.0])))
    assert r.projections[0].nwc_change == pytest.approx(-15.0)  # 0.15 x (900 - 1000)
    assert r.projections[1].nwc_change == pytest.approx(0.0)


def test_zero_rate_is_a_valid_method_not_a_missing_one():
    r = run_dcf(DcfInput(**dcf_kwargs(nwc_pct_of_incremental_revenue=0.0)))
    assert all(p.nwc_change == pytest.approx(0.0) for p in r.projections)


def test_projection_years_match_the_equivalent_per_year_schedule():
    """With constant growth, 15 per 100 of new revenue is the same dollars as 15/1100 of each year's revenue."""
    incr = run_dcf(DcfInput(**dcf_kwargs()))
    pct = 15.0 / 1100.0  # = 16.5 / 1210
    sched = run_dcf(DcfInput(**dcf_kwargs(nwc_pct_of_incremental_revenue=None, nwc_change_pct_of_revenue=[pct, pct])))
    for a, b in zip(incr.projections, sched.projections):
        assert a.fcff == pytest.approx(b.fcff) and a.nwc_change == pytest.approx(b.nwc_change)


def test_gordon_terminal_value_uses_steady_state_nwc_in_incremental_mode():
    r = run_dcf(DcfInput(**dcf_kwargs()))

    # FCFF_n+1 = (FCFF_n + NWC_n)(1+g) - rate x Rev_n x g
    #          = (152.9 + 16.5)(1.03) - 0.15 x 1210 x 0.03 = 174.482 - 5.445 = 169.037
    assert r.terminal_fcff == pytest.approx(169.037)
    # TV = 169.037 / (0.10 - 0.03) = 2414.8142857...
    assert r.terminal_value_undiscounted == pytest.approx(2414.8142857, rel=1e-9)
    # The "naive" figure (last explicit year's NWC carried forever) would have been 152.9 x 1.03 / 0.07 = 2249.81.
    assert r.terminal_value_undiscounted > 2249.8142857
    # EV = 139/1.1 + 152.9/1.21 + TV/1.21 = 252.7272727 + 1995.7142857 / ... = 2248.4415584
    assert r.enterprise_value == pytest.approx(2248.4415584, rel=1e-9)
    assert r.equity_value == pytest.approx(2248.4415584 - 200 + 100, rel=1e-9)
    assert r.implied_price_per_share == pytest.approx((2248.4415584 - 100) / 100, rel=1e-9)


def test_default_method_terminal_value_is_unchanged():
    sched = dcf_kwargs(nwc_pct_of_incremental_revenue=None, nwc_change_pct_of_revenue=[0.01, 0.01])
    r = run_dcf(DcfInput(**sched))
    last = r.projections[-1]
    assert r.terminal_fcff == pytest.approx(last.fcff * 1.03)
    assert r.terminal_value_undiscounted == pytest.approx(last.fcff * 1.03 / 0.07)


@pytest.mark.parametrize(
    "extra, message",
    [
        ({"nwc_change_pct_of_revenue": [0.01, 0.01]}, "only one NWC method"),  # both supplied
        ({"nwc_pct_of_incremental_revenue": None}, "Provide an NWC method"),  # neither supplied
    ],
)
def test_exactly_one_nwc_method_is_required(extra, message):
    kw = dcf_kwargs()
    kw.update(extra)
    kw = {k: v for k, v in kw.items() if v is not None}
    with pytest.raises(ValidationError, match=message):
        DcfInput(**kw)


@pytest.mark.parametrize("bad", [1.5, -1.01, float("nan")])
def test_incremental_rate_is_bounded(bad):
    with pytest.raises(ValidationError):
        DcfInput(**dcf_kwargs(nwc_pct_of_incremental_revenue=bad))


def test_the_other_schedules_must_still_match_the_forecast_length_in_incremental_mode():
    with pytest.raises(ValueError, match="same length"):
        run_dcf(DcfInput(**dcf_kwargs(ebit_margins=[0.2])))


def test_dcf_endpoint_accepts_either_method_and_rejects_both_or_neither():
    ok = client.post("/valuation/dcf", json=dcf_kwargs())
    assert ok.status_code == 200 and ok.json()["terminal_fcff"] == pytest.approx(169.037)
    assert client.post("/valuation/dcf", json=dcf_kwargs(nwc_change_pct_of_revenue=[0.01, 0.01])).status_code == 422
    assert client.post("/valuation/dcf", json=dcf_kwargs(nwc_pct_of_incremental_revenue=None)).status_code == 422


# ==========================================================================
# DCF warnings
# ==========================================================================


def benign(**overrides) -> dict:
    """Ten explicit years so the terminal value is a modest share of EV, a sensible WACC and spread."""
    kw = dcf_kwargs(
        revenue_growth_rates=[0.06] * 10,
        ebit_margins=[0.20] * 10,
        da_pct_of_revenue=[0.04] * 10,
        capex_pct_of_revenue=[0.05] * 10,
        wacc=0.09,
        terminal_growth_rate=0.025,
        risk_free_rate=0.04,
    )
    kw.update(overrides)
    return {k: v for k, v in kw.items() if v is not None}


def test_a_sensible_dcf_has_no_warnings():
    r = run_dcf(DcfInput(**benign()))
    assert r.warnings == []
    assert r.terminal_value_share_of_ev < 0.85


def test_warnings_never_change_a_number():
    with_rf = run_dcf(DcfInput(**benign(risk_free_rate=0.01)))  # triggers a warning
    without = run_dcf(DcfInput(**benign(risk_free_rate=None)))
    assert codes(with_rf) == {"TERMINAL_GROWTH_ABOVE_RISK_FREE"} and without.warnings == []
    assert with_rf.enterprise_value == without.enterprise_value
    assert with_rf.implied_price_per_share == without.implied_price_per_share


def test_terminal_value_share_above_85_percent_is_flagged():
    r = run_dcf(DcfInput(**dcf_kwargs()))  # two explicit years: TV dominates
    # share = (2414.8142857 / 1.21) / 2248.4415584 = 0.8876
    assert r.terminal_value_share_of_ev == pytest.approx(0.8875989, rel=1e-6)
    w = next(w for w in r.warnings if w.code == "TERMINAL_VALUE_SHARE_HIGH")
    assert "88.8%" in w.message or "88.7%" in w.message or "88.9%" in w.message


def test_terminal_value_share_is_not_flagged_below_the_threshold():
    r = run_dcf(DcfInput(**benign()))
    assert "TERMINAL_VALUE_SHARE_HIGH" not in codes(r)


def test_negative_terminal_fcff_is_flagged_and_the_terminal_value_is_negative():
    r = run_dcf(DcfInput(**benign(ebit_margins=[0.02] * 10, capex_pct_of_revenue=[0.10] * 10)))
    # Year-10 NOPAT is small and CapEx exceeds D&A, so FCFF < 0 and the perpetuity subtracts value.
    assert r.terminal_fcff < 0 and r.terminal_value_undiscounted < 0
    assert "NEGATIVE_TERMINAL_FCFF" in codes(r)


def test_negative_terminal_fcff_in_incremental_mode_uses_the_normalised_figure():
    # Steady-state NWC (rate x Rev x g) is small, so this must NOT flag when the cash flow is positive...
    assert "NEGATIVE_TERMINAL_FCFF" not in codes(run_dcf(DcfInput(**dcf_kwargs())))
    # ...and does flag once margins are thin enough that FCFF_n+1 < 0.
    thin = run_dcf(DcfInput(**dcf_kwargs(ebit_margins=[0.01, 0.01])))
    assert thin.terminal_fcff < 0 and "NEGATIVE_TERMINAL_FCFF" in codes(thin)


@pytest.mark.parametrize(
    "g, rf, expect",
    [(0.03, 0.025, True), (0.03, 0.03, False), (0.03, 0.04, False), (0.03, None, False)],
    ids=["above", "equal", "below", "not-supplied"],
)
def test_terminal_growth_above_the_risk_free_rate(g, rf, expect):
    r = run_dcf(DcfInput(**benign(terminal_growth_rate=g, risk_free_rate=rf)))
    assert ("TERMINAL_GROWTH_ABOVE_RISK_FREE" in codes(r)) is expect


def test_the_risk_free_warning_names_both_rates():
    r = run_dcf(DcfInput(**benign(terminal_growth_rate=0.03, risk_free_rate=0.025)))
    msg = next(w for w in r.warnings if w.code == "TERMINAL_GROWTH_ABOVE_RISK_FREE").message
    assert "3.0%" in msg and "2.5%" in msg


@pytest.mark.parametrize("wacc, expect", [(0.049, True), (0.05, False), (0.08, False)])
def test_low_wacc_below_five_percent(wacc, expect):
    r = run_dcf(DcfInput(**benign(wacc=wacc, terminal_growth_rate=0.02, risk_free_rate=None)))
    assert ("LOW_WACC" in codes(r)) is expect


@pytest.mark.parametrize("wacc, g, expect", [(0.06, 0.045, True), (0.09, 0.025, False), (0.07, 0.03, False)])
def test_thin_wacc_minus_growth_spread(wacc, g, expect):
    r = run_dcf(DcfInput(**benign(wacc=wacc, terminal_growth_rate=g, risk_free_rate=None)))
    assert ("THIN_WACC_SPREAD" in codes(r)) is expect


def test_negative_equity_value_is_flagged():
    r = run_dcf(DcfInput(**benign(total_debt=1e9)))
    assert r.equity_value < 0 and "NEGATIVE_EQUITY_VALUE" in codes(r)


def test_exit_multiple_terminal_value_share_is_flagged_separately():
    r = run_dcf(DcfInput(**dcf_kwargs(exit_multiple=40.0)))
    assert "EXIT_MULTIPLE_TERMINAL_VALUE_SHARE_HIGH" in codes(r)
    low = run_dcf(DcfInput(**benign(exit_multiple=2.0)))
    assert "EXIT_MULTIPLE_TERMINAL_VALUE_SHARE_HIGH" not in codes(low)


def test_share_is_none_and_not_flagged_when_enterprise_value_is_not_positive():
    r = run_dcf(DcfInput(**benign(ebit_margins=[-0.5] * 10)))
    assert r.enterprise_value < 0
    assert r.terminal_value_share_of_ev is None and "TERMINAL_VALUE_SHARE_HIGH" not in codes(r)


def test_dcf_endpoint_returns_the_warnings_as_json():
    body = client.post("/valuation/dcf", json=dcf_kwargs(risk_free_rate=0.02)).json()
    found = {w["code"]: w["message"] for w in body["warnings"]}
    assert "TERMINAL_VALUE_SHARE_HIGH" in found and "TERMINAL_GROWTH_ABOVE_RISK_FREE" in found
    assert all(isinstance(m, str) and m for m in found.values())


# ==========================================================================
# Comps: IQR outlier trimming
# ==========================================================================


def peer_with_ev_to_ebitda(name: str, multiple: float) -> CompPeer:
    """A peer whose EV/EBITDA is `multiple` and whose other multiples are constant across peers
    (EV/Revenue 2, EV/EBIT 5, P/E 15, P/B 2), so only EV/EBITDA can contain an outlier."""
    ev = multiple * 100.0
    return CompPeer(
        name=name, revenue=ev / 2, ebitda=100.0, ebit=ev / 5, net_income=ev / 15, market_cap=ev, debt=0.0, cash=0.0,
        book_value_equity=ev / 2,
    )


def comps_kwargs(multiples, **overrides) -> dict:
    kw = dict(
        peers=[peer_with_ev_to_ebitda(f"P{i}", m) for i, m in enumerate(multiples)],
        target_name="T", target_revenue=1000.0, target_ebitda=100.0, target_ebit=80.0, target_net_income=60.0,
        target_book_value_equity=500.0, target_debt=200.0, target_cash=50.0, target_diluted_shares_outstanding=10.0,
        chosen_multiple_metric="ev_to_ebitda", chosen_multiple_value=10.0,
    )
    kw.update(overrides)
    return kw


def stats_for(result, metric="ev_to_ebitda"):
    return next(s for s in result.stats if s.metric == metric)


HIGH_OUTLIER = [8, 9, 10, 11, 12, 40]  # Q1 9.25, Q3 11.75, IQR 2.5, fences [5.5, 15.5]


def test_without_trimming_the_outlier_is_in_the_statistics_and_nothing_is_trimmed():
    r = run_comps(CompsInput(**comps_kwargs(HIGH_OUTLIER)))
    s = stats_for(r)
    assert (s.min, s.p25, s.median, s.p75, s.max) == pytest.approx((8.0, 9.25, 10.5, 11.75, 40.0))
    assert r.exclusions == [] and r.excluded_outlier_peer_count == 0
    assert (s.n_peers_used, s.n_outliers_excluded, s.n_not_meaningful) == (6, 0, 0)


def test_high_outlier_is_trimmed_and_reported_with_its_fences():
    r = run_comps(CompsInput(**comps_kwargs(HIGH_OUTLIER, trim_outliers=True)))

    s = stats_for(r)  # statistics over [8, 9, 10, 11, 12]
    assert (s.min, s.p25, s.median, s.p75, s.max) == pytest.approx((8.0, 9.0, 10.0, 11.0, 12.0))
    assert (s.n_peers_used, s.n_outliers_excluded) == (5, 1)
    [ex] = r.exclusions
    assert (ex.peer, ex.metric, ex.reason) == ("P5", "ev_to_ebitda", "iqr_outlier_high")
    assert ex.value == pytest.approx(40.0)
    assert (ex.lower_bound, ex.upper_bound) == pytest.approx((5.5, 15.5))
    assert "40.00" in ex.detail and "5.50" in ex.detail and "15.50" in ex.detail
    assert r.excluded_outlier_peer_count == 1
    assert r.warnings == []


def test_low_outlier_is_trimmed_and_labelled_low():
    r = run_comps(CompsInput(**comps_kwargs([1, 9, 10, 11, 12, 13], trim_outliers=True)))
    [ex] = r.exclusions
    assert ex.reason == "iqr_outlier_low" and ex.value == pytest.approx(1.0)
    assert stats_for(r).min == pytest.approx(9.0)


def test_trimming_never_changes_the_chosen_multiple_or_the_implied_value():
    plain = run_comps(CompsInput(**comps_kwargs(HIGH_OUTLIER)))
    trimmed = run_comps(CompsInput(**comps_kwargs(HIGH_OUTLIER, trim_outliers=True)))
    # EV = 100 x 10 = 1000; equity = 1000 - 200 + 50 = 850; per share 85.
    for r in (plain, trimmed):
        assert r.implied_enterprise_value == pytest.approx(1000.0)
        assert r.implied_equity_value == pytest.approx(850.0)
        assert r.implied_price_per_share == pytest.approx(85.0)
        assert r.inputs.chosen_multiple_value == 10.0


def test_each_multiple_is_trimmed_independently():
    r = run_comps(CompsInput(**comps_kwargs(HIGH_OUTLIER, trim_outliers=True)))
    for metric in ("ev_to_revenue", "ev_to_ebit", "price_to_earnings", "price_to_book"):
        s = stats_for(r, metric)
        assert s.n_outliers_excluded == 0 and s.n_peers_used == 6  # constant across peers: no outliers


def test_a_wider_multiplier_keeps_what_a_narrower_one_trims():
    values = [8, 9, 10, 11, 12, 30]  # Q1 9.25, Q3 11.75, IQR 2.5
    wide = run_comps(CompsInput(**comps_kwargs(values, trim_outliers=True, iqr_multiplier=10.0)))
    # k=10: fences [9.25 - 25, 11.75 + 25] = [-15.75, 36.75]: 30 stays
    assert wide.exclusions == [] and stats_for(wide).max == pytest.approx(30.0)
    narrow = run_comps(CompsInput(**comps_kwargs(values, trim_outliers=True, iqr_multiplier=1.0)))
    # k=1: fences [6.75, 14.25]: 30 goes, 8..12 stay
    assert [e.peer for e in narrow.exclusions] == ["P5"]


@pytest.mark.parametrize("k", [0.0, -1.0, 10.5])
def test_iqr_multiplier_is_bounded(k):
    with pytest.raises(ValidationError):
        CompsInput(**comps_kwargs(HIGH_OUTLIER, iqr_multiplier=k))


def test_with_too_few_peers_trimming_is_skipped_loudly():
    r = run_comps(CompsInput(**comps_kwargs([10, 11, 90], trim_outliers=True)))
    assert r.exclusions == []  # the 90 was NOT silently dropped
    assert stats_for(r).max == pytest.approx(90.0) and stats_for(r).n_peers_used == 3
    skipped = [w for w in r.warnings if w.code == "TRIM_SKIPPED_TOO_FEW_PEERS"]
    assert len(skipped) == 5  # one per multiple: each has only 3 usable peers
    w = next(w for w in skipped if "ev_to_ebitda" in w.message)
    assert "3 usable" in w.message and "at least 4" in w.message


def test_trimming_that_would_leave_fewer_than_three_peers_is_skipped_loudly():
    # [0, 50, 50, 100] with k=1: Q1 37.5, Q3 62.5, fences [12.5, 87.5] would drop BOTH ends, leaving 2.
    r = run_comps(CompsInput(**comps_kwargs([0.5, 50, 50, 100], trim_outliers=True, iqr_multiplier=1.0)))
    assert r.exclusions == []
    assert stats_for(r).n_peers_used == 4
    assert "TRIM_SKIPPED_WOULD_LEAVE_TOO_FEW" in {w.code for w in r.warnings}


def test_a_value_exactly_on_a_fence_is_kept_and_one_just_outside_is_trimmed():
    values = [4, 6, 8, 10, 12]  # Q1 6, Q3 10, IQR 4
    # k = 0.5: fences are exactly [4, 12], so the two extreme peers sit ON the fences and stay.
    on_fence = run_comps(CompsInput(**comps_kwargs(values, trim_outliers=True, iqr_multiplier=0.5)))
    assert on_fence.exclusions == [] and stats_for(on_fence).n_peers_used == 5
    # k = 0.4: fences [4.4, 11.6]: both extremes are now outside and go, leaving exactly 3 peers.
    tighter = run_comps(CompsInput(**comps_kwargs(values, trim_outliers=True, iqr_multiplier=0.4)))
    assert {(e.peer, e.reason) for e in tighter.exclusions} == {("P0", "iqr_outlier_low"), ("P4", "iqr_outlier_high")}
    assert (stats_for(tighter).min, stats_for(tighter).max) == pytest.approx((6.0, 10.0))


def test_floating_point_noise_alone_never_makes_an_outlier():
    # Seven peers whose ratios agree to the last digit; one is 1 ulp away.
    r = run_comps(CompsInput(**comps_kwargs([10, 10, 10, 10, 10, 10, 10.000000000000002], trim_outliers=True)))
    assert r.exclusions == []


def test_identical_peers_are_never_called_outliers():
    r = run_comps(CompsInput(**comps_kwargs([10, 10, 10, 10, 10], trim_outliers=True)))
    assert r.exclusions == [] and stats_for(r).n_peers_used == 5


# ---- never silently drop: every peer is accounted for -------------------------------------------


def test_not_meaningful_peers_are_always_reported_even_without_trimming():
    peers = [peer_with_ev_to_ebitda(f"P{i}", m) for i, m in enumerate([8, 10, 12])]
    loss = peers[0].model_copy(update={"name": "LossMaker", "ebitda": -50.0})
    r = run_comps(CompsInput(**comps_kwargs([], peers=[*peers, loss])))

    [ex] = [e for e in r.exclusions if e.metric == "ev_to_ebitda"]
    assert (ex.peer, ex.reason, ex.value) == ("LossMaker", "not_meaningful", None)
    assert "non-positive or missing denominator" in ex.detail
    s = stats_for(r)
    assert (s.n_peers_used, s.n_not_meaningful, s.n_outliers_excluded) == (3, 1, 0)


def test_missing_book_value_is_reported_for_price_to_book():
    peers = [peer_with_ev_to_ebitda(f"P{i}", m) for i, m in enumerate([8, 10, 12])]
    peers[1] = peers[1].model_copy(update={"book_value_equity": None})
    r = run_comps(CompsInput(**comps_kwargs([], peers=peers)))
    pb = [e for e in r.exclusions if e.metric == "price_to_book"]
    assert [(e.peer, e.reason) for e in pb] == [("P1", "not_meaningful")]


@pytest.mark.parametrize("trim", [False, True])
def test_every_peer_is_accounted_for_on_every_multiple(trim):
    peers = [peer_with_ev_to_ebitda(f"P{i}", m) for i, m in enumerate([8, 9, 10, 11, 12, 40, 9.5])]
    peers[2] = peers[2].model_copy(update={"ebitda": -1.0, "net_income": 0.0})
    r = run_comps(CompsInput(**comps_kwargs([], peers=peers, trim_outliers=trim)))

    for s in r.stats:
        assert s.n_peers_used + s.n_not_meaningful + s.n_outliers_excluded == len(peers), s.metric
        reported = [e for e in r.exclusions if e.metric == s.metric]
        assert len(reported) == s.n_not_meaningful + s.n_outliers_excluded, s.metric
        assert {e.peer for e in reported} <= {p.name for p in peers}


def test_a_metric_with_no_usable_peer_reports_every_peer_and_warns_about_the_chosen_multiple():
    peers = [peer_with_ev_to_ebitda(f"P{i}", 10).model_copy(update={"ebitda": -1.0}) for i in range(3)]
    r = run_comps(CompsInput(**comps_kwargs([], peers=peers)))
    assert all(s.metric != "ev_to_ebitda" for s in r.stats)  # nothing to summarise
    assert len([e for e in r.exclusions if e.metric == "ev_to_ebitda"]) == 3  # but all three are listed
    assert "CHOSEN_METRIC_HAS_NO_PEER_STATS" in {w.code for w in r.warnings}


def test_comps_warnings_for_few_peers_and_a_chosen_multiple_outside_the_range():
    r = run_comps(CompsInput(**comps_kwargs([9, 11], chosen_multiple_value=14.0)))
    found = {w.code for w in r.warnings}
    assert {"FEW_PEERS", "CHOSEN_MULTIPLE_OUTSIDE_PEER_RANGE"} <= found
    ok = run_comps(CompsInput(**comps_kwargs([9, 10, 11, 12], chosen_multiple_value=10.5)))
    assert ok.warnings == []


def test_compute_multiple_stats_keeps_its_original_behaviour():
    pms = [compute_peer_multiples(p) for p in comps_kwargs(HIGH_OUTLIER)["peers"]]
    s = compute_multiple_stats(pms, "ev_to_ebitda")
    assert (s.min, s.median, s.max) == pytest.approx((8.0, 10.5, 40.0))  # untrimmed, as before


def test_comps_endpoint_trims_and_reports():
    body = comps_kwargs(HIGH_OUTLIER, trim_outliers=True)
    body["peers"] = [p.model_dump() for p in body["peers"]]
    out = client.post("/valuation/comps", json=body).json()
    [ex] = out["exclusions"]
    assert ex["peer"] == "P5" and ex["reason"] == "iqr_outlier_high" and out["excluded_outlier_peer_count"] == 1
    s = next(s for s in out["stats"] if s["metric"] == "ev_to_ebitda")
    assert s["n_peers_used"] == 5 and s["median"] == pytest.approx(10.0)


# ==========================================================================
# SOTP warnings
# ==========================================================================


def sotp_kwargs(**overrides) -> dict:
    """Segments 1000 (100%) + 500 (60% -> 300) = 1300; cash 200, securities 50 -> 250 non-operating;
    deductions 400 + 20 + 30 = 450. Direct-deduction equity 1100 -> $11.00; capitalised overhead (50 x 5 = 250) -> 850."""
    kw = dict(
        segments=[SotpSegmentInput(name="A", enterprise_value=1000.0, ownership_pct=1.0),
                  SotpSegmentInput(name="B", enterprise_value=500.0, ownership_pct=0.6)],
        cash_and_equivalents=200.0, marketable_securities=50.0, other_investments=0.0, total_debt=400.0,
        minority_interest=20.0, corporate_liabilities=30.0, corporate_overhead_annual=50.0,
        overhead_capitalization_multiple=5.0, corporate_overhead_treatment="capitalized_overhead",
        diluted_shares_outstanding=100.0, current_share_price=8.5,
    )
    kw.update(overrides)
    return kw


def test_a_clean_sotp_has_no_warnings():
    r = run_sotp(SotpInput(**sotp_kwargs()))
    assert r.equity_value == pytest.approx(850.0) and r.warnings == []


def test_overhead_entered_but_not_deducted_is_flagged_with_the_amount_at_stake():
    r = run_sotp(SotpInput(**sotp_kwargs(corporate_overhead_treatment="direct_deduction", current_share_price=11.0)))
    assert r.equity_value == pytest.approx(1100.0)
    w = next(w for w in r.warnings if w.code == "OVERHEAD_NOT_DEDUCTED")
    assert "50" in w.message and "250" in w.message  # annual overhead, and the 50 x 5 it would cost
    zero = run_sotp(SotpInput(**sotp_kwargs(corporate_overhead_treatment="direct_deduction", corporate_overhead_annual=0.0,
                                            current_share_price=11.0)))
    assert "OVERHEAD_NOT_DEDUCTED" not in codes(zero)


@pytest.mark.parametrize(
    "price, expect",
    [(4.0, True), (20.0, True), (8.5, False), (6.0, False)],  # implied 8.5: +112% / -57.5% / 0% / +41.7%
    ids=["market-far-below", "market-far-above", "in-line", "within-50-percent"],
)
def test_large_gap_to_market_price(price, expect):
    r = run_sotp(SotpInput(**sotp_kwargs(current_share_price=price)))
    assert ("LARGE_GAP_TO_MARKET" in codes(r)) is expect


def test_no_market_price_means_no_gap_warning():
    r = run_sotp(SotpInput(**sotp_kwargs(current_share_price=None)))
    assert "LARGE_GAP_TO_MARKET" not in codes(r)


def test_negative_equity_and_negative_segments_are_flagged():
    r = run_sotp(SotpInput(**sotp_kwargs(total_debt=5000.0)))
    assert r.equity_value < 0 and "NEGATIVE_EQUITY_VALUE" in codes(r)
    seg = SotpSegmentInput(name="Drag", enterprise_value=-100.0, ownership_pct=1.0)
    bad = run_sotp(SotpInput(**sotp_kwargs(segments=[*sotp_kwargs()["segments"], seg])))
    w = next(w for w in bad.warnings if w.code == "NEGATIVE_SEGMENT_EV")
    assert "Drag" in w.message and "A" not in w.message.split(":")[1]


def test_sotp_warnings_do_not_change_any_figure():
    a = run_sotp(SotpInput(**sotp_kwargs(corporate_overhead_treatment="direct_deduction")))
    b = run_sotp(SotpInput(**sotp_kwargs(corporate_overhead_treatment="direct_deduction", corporate_overhead_annual=0.0)))
    assert a.equity_value == b.equity_value and codes(a) != codes(b)


def test_sotp_endpoint_returns_warnings():
    body = sotp_kwargs(corporate_overhead_treatment="direct_deduction", current_share_price=2.0)
    body["segments"] = [s.model_dump() for s in body["segments"]]
    out = client.post("/valuation/sotp", json=body).json()
    assert {w["code"] for w in out["warnings"]} == {"OVERHEAD_NOT_DEDUCTED", "LARGE_GAP_TO_MARKET"}
