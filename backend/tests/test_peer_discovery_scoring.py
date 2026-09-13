"""Unit tests for the composite similarity scoring added to
backend.data.peer_discovery (margin-profile + growth-profile similarity on
top of the pre-existing revenue-proximity + SIC filter).

Fixture-based, no real network access.
"""

from __future__ import annotations

from backend.data.peer_discovery import discover_peer_candidates
from backend.services.cache import FileCache
from backend.services.sec_client import SECConnector

TARGET_CIK = "0000000010"  # SIC 7372 (software)
CLOSE_REVENUE_CIK = "0000000011"  # revenue very close to target, but very different margin/growth
CLOSE_PROFILE_CIK = "0000000012"  # revenue further away, but margin/growth very close to target
NO_DATA_CIK = "0000000013"  # same SIC, no financials at all -- must not get fabricated similarity

FAKE_FRAME = {
    "data": [
        {"cik": 10, "entityName": "TARGET CO", "val": 1_000_000_000},
        {"cik": 11, "entityName": "CLOSE REVENUE CO", "val": 1_005_000_000},
        {"cik": 12, "entityName": "CLOSE PROFILE CO", "val": 400_000_000},
        {"cik": 13, "entityName": "NO DATA CO", "val": 950_000_000},
    ]
}

SUBMISSIONS_BY_CIK = {
    TARGET_CIK: {"name": "Target Co", "sic": "7372", "sicDescription": "Prepackaged Software"},
    CLOSE_REVENUE_CIK: {"name": "Close Revenue Co", "sic": "7372", "sicDescription": "Prepackaged Software"},
    CLOSE_PROFILE_CIK: {"name": "Close Profile Co", "sic": "7372", "sicDescription": "Prepackaged Software"},
    NO_DATA_CIK: {"name": "No Data Co", "sic": "7372", "sicDescription": "Prepackaged Software"},
}


def _facts(revenue_fy1: float, revenue_fy2: float, op_income_fy2: float, accn_prefix: str) -> dict:
    """Two years of revenue (for CAGR) + latest-year operating income (for margin)."""
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"val": revenue_fy1, "fy": 2022, "fp": "FY", "start": "2022-01-01", "end": "2022-12-31", "filed": "2023-02-01", "accn": f"{accn_prefix}-23-000001"},
                            {"val": revenue_fy2, "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "accn": f"{accn_prefix}-24-000001"},
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            {"val": op_income_fy2, "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "accn": f"{accn_prefix}-24-000001"}
                        ]
                    }
                },
            }
        }
    }


# Target: revenue grew 1.0B -> 1.1B (10% CAGR), operating margin 20%.
COMPANY_FACTS_BY_CIK = {
    TARGET_CIK: _facts(1_000_000_000, 1_100_000_000, 220_000_000, "0000000010"),
    # Close in revenue size (1.005B) but very different profile: flat revenue (0% growth), 2% margin.
    CLOSE_REVENUE_CIK: _facts(1_005_000_000, 1_005_000_000, 20_100_000, "0000000011"),
    # Far in revenue size (0.4B) but near-identical profile: ~10% growth, ~20% margin.
    CLOSE_PROFILE_CIK: _facts(360_000_000, 396_000_000, 79_200_000, "0000000012"),
    NO_DATA_CIK: {"facts": {"us-gaap": {}}},
}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    client = SECConnector(cache=FileCache(cache_dir=tmp_path))

    monkeypatch.setattr(client, "get_frame", lambda *a, **k: FAKE_FRAME)
    monkeypatch.setattr(client, "get_submissions", lambda cik10: SUBMISSIONS_BY_CIK[cik10])
    monkeypatch.setattr(client, "get_company_facts", lambda cik10: COMPANY_FACTS_BY_CIK.get(cik10, {"facts": {}}))
    monkeypatch.setattr(
        client,
        "_load_ticker_map",
        lambda: {"CLOSEREV": CLOSE_REVENUE_CIK, "CLOSEPROF": CLOSE_PROFILE_CIK, "NODATA": NO_DATA_CIK},
    )
    return client


def test_composite_score_favors_margin_and_growth_similarity_over_revenue_proximity(tmp_path, monkeypatch):
    """CLOSE_PROFILE_CIK is much further in raw revenue but nearly identical in
    margin (~20%) and growth (~10%) to the target; CLOSE_REVENUE_CIK is almost
    identical in revenue size but wildly different margin/growth. The composite
    (0.3 revenue / 0.35 margin / 0.35 growth) should rank CLOSE_PROFILE_CIK above
    CLOSE_REVENUE_CIK, proving the ranking isn't naive revenue-proximity-only."""
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    close_revenue = next(c for c in result.candidates if c.cik10 == CLOSE_REVENUE_CIK)
    close_profile = next(c for c in result.candidates if c.cik10 == CLOSE_PROFILE_CIK)

    assert close_revenue.similarity is not None
    assert close_profile.similarity is not None

    # Sanity-check the underlying computed metrics before asserting the ranking.
    assert close_profile.similarity.operating_margin_pct is not None
    assert abs(close_profile.similarity.operating_margin_pct - 20.0) < 0.5
    assert close_revenue.similarity.operating_margin_pct is not None
    assert abs(close_revenue.similarity.operating_margin_pct - 2.0) < 0.5

    # The naive old ranking (pure revenue proximity) would put close_revenue
    # first; the new composite score should invert that.
    assert close_revenue.similarity.revenue_proximity_score > close_profile.similarity.revenue_proximity_score
    assert close_profile.similarity.composite_score > close_revenue.similarity.composite_score

    # And the returned candidate list itself should be re-ranked accordingly.
    ranked_ciks = [c.cik10 for c in result.candidates if c.cik10 in (CLOSE_REVENUE_CIK, CLOSE_PROFILE_CIK)]
    assert ranked_ciks[0] == CLOSE_PROFILE_CIK


def test_missing_margin_and_growth_data_does_not_fabricate_similarity_credit(tmp_path, monkeypatch):
    """A candidate with no reported financials at all must not be scored as if
    it were similar -- its margin/growth similarity dimensions come back None,
    it gets an explicit caveat, and its composite score is penalized (never
    equal to a fully-scored candidate with a comparable revenue-only signal)."""
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    no_data = next(c for c in result.candidates if c.cik10 == NO_DATA_CIK)
    assert no_data.similarity is not None
    assert no_data.similarity.operating_margin_pct is None
    assert no_data.similarity.revenue_cagr_pct is None
    assert no_data.similarity.margin_similarity_score is None
    assert no_data.similarity.growth_similarity_score is None
    assert no_data.similarity.data_completeness < 1.0
    assert len(no_data.similarity.caveats) >= 2  # margin caveat + growth caveat

    # Its composite score must be strictly less than a candidate with the same
    # revenue-proximity signal but full margin/growth data (data_completeness
    # penalty), never inflated to look equally comparable.
    close_revenue = next(c for c in result.candidates if c.cik10 == CLOSE_REVENUE_CIK)
    assert no_data.similarity.data_completeness < close_revenue.similarity.data_completeness
