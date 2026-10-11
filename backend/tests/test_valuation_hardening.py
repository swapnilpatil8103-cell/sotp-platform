"""Hardening of the valuation API: no input may produce a 500.

One group per hardening item:
  1. field constraints on DcfInput / WaccInput (tax rate, wacc, growth,
     margins, exit multiple) and arithmetic failures returning 400;
  2. POST /valuation/wacc takes a typed WaccInput;
  3. the DCF sensitivity request only accepts real scalar fields, capped axes;
  4. unknown `method` on the runs endpoints is a 400;
  5. comps multiples exclude non-positive denominators.

The client uses raise_server_exceptions=False so an unhandled error shows up
as an actual 500 status rather than a test-side exception. Schema-constraint
violations are FastAPI's standard 422; failures that only appear when the
engine runs (zero division, overflow, a bad value inside a sensitivity grid)
are a 400.
"""

from __future__ import annotations

import json
import types
import typing

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.api.routers.valuation import MAX_SENSITIVITY_AXIS_VALUES
from backend.governance.run_computation import InvalidRunInputsError, compute_run
from backend.models.enums import ValuationMethod
from backend.schemas.valuation import (
    CompPeer,
    CompsInput,
    DcfInput,
    DcfResult,
    DcfScalarField,
    DcfSensitivityOutput,
    WaccInput,
)
from backend.tests.fixtures.auth import auth_headers
from backend.tests.fixtures.run_inputs import comps_inputs, dcf_inputs
from backend.valuation.comps import compute_multiple_stats, compute_peer_multiples, run_comps
from backend.valuation.dcf import _run_dcf, run_dcf
from backend.valuation.wacc import compute_wacc

client = TestClient(app, raise_server_exceptions=False, headers=auth_headers())

DCF = "/valuation/dcf"
WACC = "/valuation/wacc"
COMPS = "/valuation/comps"
SENS = "/valuation/sensitivity/dcf"


def _wacc_inputs(**overrides):
    body = {
        "risk_free_rate": 0.04,
        "beta": 1.2,
        "equity_risk_premium": 0.05,
        "cost_of_debt_pretax": 0.05,
        "tax_rate": 0.2,
        "market_value_equity": 800.0,
        "market_value_debt": 200.0,
    }
    body.update(overrides)
    return body


def _sens(**overrides):
    body = {
        "base_dcf_input": dcf_inputs(),
        "row_field": "wacc",
        "row_values": [0.08, 0.09],
        "col_field": "terminal_growth_rate",
        "col_values": [0.02, 0.03],
    }
    body.update(overrides)
    return body


def _post_non_finite(url: str, body: dict, token: str):
    """POST ``body`` with every "@@NF@@" string replaced by a bare NaN/Infinity
    token. httpx refuses to serialise those itself, but a hostile client can
    send them, and Python's JSON parser accepts them."""
    text = json.dumps(body).replace('"@@NF@@"', token)
    return client.post(url, content=text, headers={"Content-Type": "application/json"})


# ==========================================================================
# 1. Field constraints, and arithmetic failures are 400 not 500
# ==========================================================================


@pytest.mark.parametrize("tax_rate", [-0.01, -1.0, 1.0, 1.5, 100.0])
def test_dcf_tax_rate_outside_0_to_1_is_rejected(tax_rate):
    resp = client.post(DCF, json=dcf_inputs(tax_rate=tax_rate))
    assert resp.status_code == 422, resp.text
    assert "tax_rate" in resp.text


@pytest.mark.parametrize("tax_rate", [0.0, 0.21, 0.999])
def test_dcf_tax_rate_inside_0_to_1_is_accepted(tax_rate):
    assert client.post(DCF, json=dcf_inputs(tax_rate=tax_rate)).status_code == 200


@pytest.mark.parametrize("tax_rate", [-0.01, 1.0, 1.5])
def test_wacc_tax_rate_outside_0_to_1_is_rejected(tax_rate):
    resp = client.post(WACC, json=_wacc_inputs(tax_rate=tax_rate))
    assert resp.status_code == 422, resp.text
    assert "tax_rate" in resp.text


@pytest.mark.parametrize("tax_rate", [0.0, 0.2, 0.999])
def test_wacc_tax_rate_inside_0_to_1_is_accepted(tax_rate):
    assert client.post(WACC, json=_wacc_inputs(tax_rate=tax_rate)).status_code == 200


@pytest.mark.parametrize("wacc", [-1.0, -1.5, -100.0])
def test_dcf_wacc_at_or_below_minus_one_is_rejected(wacc):
    """wacc == -1 used to divide by zero in the discount factor: a 500."""
    resp = client.post(DCF, json=dcf_inputs(wacc=wacc))
    assert resp.status_code == 422, resp.text
    assert "wacc" in resp.text


def test_dcf_negative_wacc_above_minus_one_is_accepted_when_it_computes():
    resp = client.post(DCF, json=dcf_inputs(wacc=-0.5, terminal_growth_rate=-0.6))
    assert resp.status_code == 200, resp.text


def _long_horizon(**overrides):
    n = 25
    return dcf_inputs(
        revenue_growth_rates=[0.05] * n,
        ebit_margins=[0.2] * n,
        da_pct_of_revenue=[0.05] * n,
        capex_pct_of_revenue=[0.06] * n,
        nwc_change_pct_of_revenue=[0.01] * n,
        **overrides,
    )


def test_zero_division_in_the_discount_factor_is_a_400():
    """wacc just above -1 passes the schema, but over a long horizon
    (1 + wacc) ** n underflows to 0.0 and 1 / 0.0 raises ZeroDivisionError."""
    inputs = _long_horizon(wacc=-0.9999999999999999)

    # the raw failure really is a ZeroDivisionError ...
    with pytest.raises(ZeroDivisionError):
        _run_dcf(DcfInput(**inputs))
    # ... which the public engine entry point turns into a ValueError ...
    with pytest.raises(ValueError, match="could not be computed"):
        run_dcf(DcfInput(**inputs))
    # ... which the endpoint reports as a 400.
    resp = client.post(DCF, json=inputs)
    assert resp.status_code == 400, resp.text
    assert "could not be computed" in resp.json()["detail"]


def test_overflow_in_the_discount_factor_is_a_400():
    resp = client.post(DCF, json=dcf_inputs(wacc=1e300))
    assert resp.status_code == 400, resp.text


def test_a_non_finite_result_is_a_400_not_a_200_of_nulls():
    resp = client.post(DCF, json=dcf_inputs(base_revenue=1e308, revenue_growth_rates=[10.0, 10.0]))
    assert resp.status_code == 400, resp.text
    assert "non-finite" in resp.json()["detail"]


@pytest.mark.parametrize("growth", [-1.0, -1.5, -5.0, 10.01, 1e6])
def test_dcf_growth_outside_bounds_is_rejected(growth):
    resp = client.post(DCF, json=dcf_inputs(revenue_growth_rates=[0.1, growth]))
    assert resp.status_code == 422, resp.text
    assert "revenue_growth_rates" in resp.text


@pytest.mark.parametrize("growth", [-0.99, 0.0, 10.0])
def test_dcf_growth_at_the_bounds_is_accepted(growth):
    assert client.post(DCF, json=dcf_inputs(revenue_growth_rates=[growth, growth])).status_code == 200


@pytest.mark.parametrize("margin", [1.01, 50.0, -5.01, -100.0])
def test_dcf_margin_outside_bounds_is_rejected(margin):
    resp = client.post(DCF, json=dcf_inputs(ebit_margins=[0.2, margin]))
    assert resp.status_code == 422, resp.text
    assert "ebit_margins" in resp.text


@pytest.mark.parametrize("margin", [-5.0, 0.0, 1.0])
def test_dcf_margin_at_the_bounds_is_accepted(margin):
    assert client.post(DCF, json=dcf_inputs(ebit_margins=[margin, margin])).status_code == 200


@pytest.mark.parametrize("terminal_growth", [-1.0, -2.0, 1.01])
def test_dcf_terminal_growth_outside_bounds_is_rejected(terminal_growth):
    resp = client.post(DCF, json=dcf_inputs(terminal_growth_rate=terminal_growth))
    assert resp.status_code == 422, resp.text
    assert "terminal_growth_rate" in resp.text


@pytest.mark.parametrize("exit_multiple", [0.0, -5.0, 1000.01, 1e9])
def test_dcf_exit_multiple_outside_bounds_is_rejected(exit_multiple):
    resp = client.post(DCF, json=dcf_inputs(exit_multiple=exit_multiple))
    assert resp.status_code == 422, resp.text
    assert "exit_multiple" in resp.text


@pytest.mark.parametrize("exit_multiple", [0.5, 18.0, 1000.0])
def test_dcf_exit_multiple_inside_bounds_is_accepted(exit_multiple):
    resp = client.post(DCF, json=dcf_inputs(exit_multiple=exit_multiple))
    assert resp.status_code == 200, resp.text
    assert resp.json()["exit_multiple_enterprise_value"] is not None


def test_dcf_exit_multiple_is_still_optional():
    resp = client.post(DCF, json=dcf_inputs())
    assert resp.status_code == 200
    assert resp.json()["exit_multiple_enterprise_value"] is None


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("field", ["wacc", "tax_rate", "base_revenue", "terminal_growth_rate"])
def test_dcf_non_finite_numbers_are_rejected_not_a_500(field, token):
    """A 422 echoes the offending input back, and a bare NaN could not be
    encoded: the default handler crashed and the client got a 500."""
    resp = _post_non_finite(DCF, dcf_inputs(**{field: "@@NF@@"}), token)
    assert resp.status_code == 422, resp.text
    assert field in resp.text


def test_dcf_non_finite_inside_a_schedule_is_rejected_not_a_500():
    resp = _post_non_finite(DCF, dcf_inputs(revenue_growth_rates=[0.1, "@@NF@@"]), "NaN")
    assert resp.status_code == 422, resp.text


def test_bounds_do_not_reject_the_frontend_defaults():
    """Guards against bounds tight enough to break the shipped UI."""
    frontend_defaults = {
        "base_revenue": 100_000_000_000,
        "revenue_growth_rates": [0.08] * 5,
        "ebit_margins": [0.24] * 5,
        "tax_rate": 0.21,
        "da_pct_of_revenue": [0.04] * 5,
        "capex_pct_of_revenue": [0.05] * 5,
        "nwc_change_pct_of_revenue": [0.01] * 5,
        "wacc": 0.09,
        "terminal_growth_rate": 0.025,
        "total_debt": 5_000_000_000,
        "cash_and_equivalents": 20_000_000_000,
        "investments": 0,
        "minority_interest": 0,
        "diluted_shares_outstanding": 1_000_000_000,
    }
    assert client.post(DCF, json=frontend_defaults).status_code == 200


def test_bounds_do_not_reject_a_realistic_forecast_with_an_exit_multiple():
    realistic = dcf_inputs(
        base_revenue=402_836_000_000,
        revenue_growth_rates=[0.13, 0.12, 0.10, 0.08, 0.07],
        ebit_margins=[0.31, 0.31, 0.30, 0.30, 0.30],
        da_pct_of_revenue=[0.0525] * 5,
        capex_pct_of_revenue=[0.20, 0.18, 0.16, 0.15, 0.14],
        nwc_change_pct_of_revenue=[0.01] * 5,
        tax_rate=0.1678,
        wacc=0.10247,
        terminal_growth_rate=0.03,
        exit_multiple=18,
    )
    assert client.post(DCF, json=realistic).status_code == 200


def test_governed_run_turns_bound_violations_and_arithmetic_failures_into_input_errors():
    """The run path must raise InvalidRunInputsError (a 400), never the raw
    ValidationError or ZeroDivisionError."""
    with pytest.raises(InvalidRunInputsError):
        compute_run(ValuationMethod.DCF, dcf_inputs(tax_rate=1.5))
    with pytest.raises(InvalidRunInputsError):
        compute_run(ValuationMethod.DCF, _long_horizon(wacc=-0.9999999999999999))


def test_segment_auto_value_reports_a_bad_dcf_assumption_per_segment_not_a_500():
    """DcfInput is built inside segment valuation; with bounds it can raise a
    ValidationError, which must become that segment's error."""
    body = {
        "segments": [
            {
                "segment": {"name": "Cloud", "revenue": 58.7e9, "revenue_status": "REPORTED"},
                "methodology": "dcf",
                "dcf_assumption": {
                    "revenue_growth_rates": [0.2, 0.15],
                    "ebit_margins": [0.2, 0.2],
                    "tax_rate": 1.5,
                    "da_pct_of_revenue": [0.05, 0.05],
                    "capex_pct_of_revenue": [0.1, 0.1],
                    "nwc_change_pct_of_revenue": [0.01, 0.01],
                    "wacc": 0.09,
                    "terminal_growth_rate": 0.03,
                },
            }
        ]
    }
    resp = client.post("/valuation/GOOGL/segments/auto-value", json=body)
    assert resp.status_code == 200, resp.text
    suggestion = resp.json()["suggestions"][0]
    assert suggestion["status"] == "ERROR"
    assert "tax_rate" in suggestion["error"]
    assert suggestion["suggested_enterprise_value"] is None


# ==========================================================================
# 2. POST /valuation/wacc takes a typed WaccInput
# ==========================================================================


def test_wacc_endpoint_schema_is_the_typed_model_not_a_free_dict():
    operation = app.openapi()["paths"][WACC]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/WaccInput")
    assert response_schema["$ref"].endswith("/WaccResult")


def test_wacc_endpoint_computes_the_same_result_as_the_engine():
    body = _wacc_inputs()
    resp = client.post(WACC, json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["wacc"] == pytest.approx(compute_wacc(WaccInput(**body)).wacc)


@pytest.mark.parametrize(
    "body",
    [
        {"beta": 1.0},  # missing required fields
        _wacc_inputs(beta="abc"),  # wrong type
        _wacc_inputs(market_value_equity=0.0),  # must be > 0
        _wacc_inputs(market_value_debt=-1.0),  # must be >= 0
        [1, 2, 3],  # not an object
        "just a string",
        None,
    ],
    ids=["missing-fields", "bad-type", "zero-equity", "negative-debt", "list-body", "string-body", "null-body"],
)
def test_wacc_malformed_bodies_are_422_not_a_500(body):
    resp = client.post(WACC, json=body)
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("token", ["NaN", "Infinity"])
def test_wacc_non_finite_numbers_are_422_not_a_500(token):
    resp = _post_non_finite(WACC, _wacc_inputs(beta="@@NF@@"), token)
    assert resp.status_code == 422, resp.text


# ==========================================================================
# 3. Sensitivity request: real scalar fields only, capped axes
# ==========================================================================


def _scalar_float_fields(model) -> set[str]:
    fields = set()
    for name, info in model.model_fields.items():
        annotation = info.annotation
        optional_float = typing.get_origin(annotation) in (typing.Union, types.UnionType) and set(
            typing.get_args(annotation)
        ) == {float, type(None)}
        if annotation is float or optional_float:
            fields.add(name)
    return fields


def test_sensitivity_field_literal_matches_the_scalar_dcf_input_fields():
    """The static Literal must not drift from DcfInput (a new scalar input
    would silently be un-sweepable; a removed one would become a 400).

    risk_free_rate is the one deliberate exception: it only drives a warning and
    never enters the arithmetic, so sweeping it would produce a flat grid."""
    not_sweepable = {"risk_free_rate"}
    assert set(typing.get_args(DcfScalarField)) == _scalar_float_fields(DcfInput) - not_sweepable


def test_sensitivity_output_literal_names_real_scalar_dcf_result_fields():
    outputs = set(typing.get_args(DcfSensitivityOutput))
    assert outputs <= _scalar_float_fields(DcfResult)
    assert "implied_price_per_share" in outputs


_AXIS_VALUES = {
    "base_revenue": [900.0, 1000.0],
    "tax_rate": [0.2, 0.25],
    "wacc": [0.08, 0.09],
    "terminal_growth_rate": [0.02, 0.03],
    "total_debt": [100.0, 200.0],
    "cash_and_equivalents": [50.0, 100.0],
    "investments": [0.0, 10.0],
    "minority_interest": [0.0, 5.0],
    "diluted_shares_outstanding": [90.0, 100.0],
    "exit_multiple": [8.0, 10.0],
    "nwc_pct_of_incremental_revenue": [0.10, 0.20],
}


def _incremental_nwc_base() -> dict:
    """A base case that uses the incremental-NWC method instead of the per-year schedule."""
    base = dcf_inputs()
    base.pop("nwc_change_pct_of_revenue")
    base["nwc_pct_of_incremental_revenue"] = 0.15
    return base


@pytest.mark.parametrize("field", typing.get_args(DcfScalarField))
def test_every_allowed_axis_field_works(field):
    other = "tax_rate" if field == "wacc" else "wacc"
    # The incremental-NWC rate can only be swept when the base case uses that method.
    extra = {"base_dcf_input": _incremental_nwc_base()} if field == "nwc_pct_of_incremental_revenue" else {}
    resp = client.post(
        SENS,
        json=_sens(
            row_field=field, row_values=_AXIS_VALUES[field], col_field=other, col_values=_AXIS_VALUES[other], **extra
        ),
    )
    assert resp.status_code == 200, resp.text
    matrix = resp.json()["matrix"]
    assert len(matrix) == 2 and all(len(row) == 2 for row in matrix)


def test_sensitivity_grid_matches_direct_engine_runs():
    resp = client.post(SENS, json=_sens(row_values=[0.08, 0.09], col_values=[0.02, 0.03]))
    assert resp.status_code == 200, resp.text
    matrix = resp.json()["matrix"]
    for i, wacc in enumerate([0.08, 0.09]):
        for j, growth in enumerate([0.02, 0.03]):
            direct = run_dcf(DcfInput(**dcf_inputs(wacc=wacc, terminal_growth_rate=growth))).implied_price_per_share
            assert matrix[i][j] == pytest.approx(direct)


@pytest.mark.parametrize("axis", ["row_field", "col_field"])
@pytest.mark.parametrize(
    "bad_field",
    ["bogus", "revenue_growth_rates", "ebit_margins", "inputs", "", "WACC", "wacc; drop"],
    ids=["unknown", "list-field", "list-field-2", "nested-model", "empty", "wrong-case", "injection-ish"],
)
def test_sensitivity_axis_must_be_a_real_scalar_field(axis, bad_field):
    other_axis = {"row_field": "col_field", "col_field": "row_field"}[axis]
    # keep the other axis valid and different, so only `axis` is wrong
    body = _sens(**{axis: bad_field, other_axis: "tax_rate"})
    resp = client.post(SENS, json=body)
    assert resp.status_code == 422, resp.text
    assert axis in resp.text


def test_sensitivity_axes_must_be_different_fields():
    resp = client.post(SENS, json=_sens(row_field="wacc", col_field="wacc"))
    assert resp.status_code == 422, resp.text
    assert "different" in resp.text


@pytest.mark.parametrize("bad_output", ["bogus", "projections", "inputs", "terminal_year_ebitda", ""])
def test_sensitivity_output_field_must_be_a_real_scalar_result_field(bad_output):
    """An unknown output used to hit getattr() with no handler: a 500."""
    resp = client.post(SENS, json=_sens(output_field=bad_output))
    assert resp.status_code == 422, resp.text
    assert "output_field" in resp.text


@pytest.mark.parametrize("output_field", typing.get_args(DcfSensitivityOutput))
def test_every_allowed_output_field_works(output_field):
    resp = client.post(SENS, json=_sens(output_field=output_field))
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("axis", ["row_values", "col_values"])
def test_sensitivity_axis_is_capped_at_25_values(axis):
    assert MAX_SENSITIVITY_AXIS_VALUES == 25
    over = client.post(SENS, json=_sens(**{axis: [0.05 + i * 0.001 for i in range(26)]}))
    assert over.status_code == 422, over.text
    assert axis in over.text

    exactly = client.post(SENS, json=_sens(**{axis: [0.05 + i * 0.001 for i in range(25)]}))
    assert exactly.status_code == 200, exactly.text


def test_sensitivity_full_25_by_25_grid_runs():
    values = [0.05 + i * 0.001 for i in range(25)]
    resp = client.post(SENS, json=_sens(row_values=values, col_values=[0.01 + i * 0.0005 for i in range(25)]))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["matrix"]) == 25


@pytest.mark.parametrize("axis", ["row_values", "col_values"])
def test_sensitivity_axis_must_not_be_empty(axis):
    resp = client.post(SENS, json=_sens(**{axis: []}))
    assert resp.status_code == 422, resp.text


def test_sensitivity_grid_value_of_minus_one_wacc_is_a_400_not_a_500():
    """The grid bypasses the request-level bounds (values are fed into DcfInput
    per cell); wacc == -1 here used to be a ZeroDivisionError."""
    resp = client.post(SENS, json=_sens(row_values=[-1.0, 0.09]))
    assert resp.status_code == 400, resp.text


def test_sensitivity_grid_value_outside_a_field_bound_is_a_400():
    resp = client.post(SENS, json=_sens(row_field="tax_rate", row_values=[0.2, 1.5], col_field="wacc", col_values=[0.09]))
    assert resp.status_code == 400, resp.text


def test_sensitivity_incomplete_base_input_is_a_400():
    base = dcf_inputs()
    del base["base_revenue"]
    resp = client.post(SENS, json=_sens(base_dcf_input=base))
    assert resp.status_code == 400, resp.text


@pytest.mark.parametrize("token", ["NaN", "Infinity"])
def test_sensitivity_non_finite_axis_value_is_422_not_a_500(token):
    resp = _post_non_finite(SENS, _sens(row_values=[0.08, "@@NF@@"]), token)
    assert resp.status_code == 422, resp.text


# ==========================================================================
# 4. Unknown `method` on the runs endpoints is a 400
# ==========================================================================

RUNS = "/valuation/company/1/runs"
DIFF = "/valuation/company/1/runs/diff"


def _diff_params(method: str) -> dict:
    # httpx replaces the URL's own query string when params= is given, so the
    # version numbers must travel with the method.
    return {"from_version": 1, "to_version": 2, "method": method}

BAD_METHODS = ["LBO", "dcf2", "DCF ", "123", "DCF;DROP TABLE", "sotp/comps", "%00"]


@pytest.mark.parametrize("method", BAD_METHODS)
def test_runs_unknown_method_is_a_400(method):
    resp = client.get(RUNS, params={"method": method})
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "Unknown method" in detail
    assert all(m in detail for m in ("DCF", "COMPS", "SOTP"))


@pytest.mark.parametrize("method", BAD_METHODS)
def test_runs_diff_unknown_method_is_a_400(method):
    resp = client.get(DIFF, params=_diff_params(method))
    assert resp.status_code == 400, resp.text
    assert "Unknown method" in resp.json()["detail"]


def test_diff_with_unknown_method_is_a_400_even_when_the_versions_do_not_exist():
    """The method is validated before any lookup, so it is not masked by a 404."""
    assert client.get(DIFF, params=_diff_params("LBO")).status_code == 400
    assert client.get(DIFF, params=_diff_params("DCF")).status_code == 404


@pytest.mark.parametrize("method", ["DCF", "dcf", "Dcf", "COMPS", "sotp"])
def test_runs_valid_method_is_accepted_case_insensitively(method):
    assert client.get(RUNS, params={"method": method}).status_code == 200


def test_runs_without_a_method_is_unfiltered():
    assert client.get(RUNS).status_code == 200
    assert client.get(RUNS, params={"method": ""}).status_code == 200


def test_run_endpoint_unknown_method_is_still_a_400():
    resp = client.post("/valuation/run", json={"company_id": 1, "method": "LBO", "decision_ids": [1], "inputs": {}})
    assert resp.status_code == 400, resp.text
    assert "Unknown method" in resp.json()["detail"]


# ==========================================================================
# 5. Comps: non-positive denominators never become multiples
# ==========================================================================


def _peer(**overrides) -> CompPeer:
    base = dict(
        name="P", revenue=1000.0, ebitda=300.0, ebit=250.0, net_income=150.0,
        market_cap=3000.0, debt=300.0, cash=100.0, book_value_equity=1500.0,
    )
    base.update(overrides)
    return CompPeer(**base)


def test_positive_denominators_still_produce_multiples():
    m = compute_peer_multiples(_peer())
    assert m.ev_to_revenue == pytest.approx(3200.0 / 1000.0)
    assert m.ev_to_ebitda == pytest.approx(3200.0 / 300.0)
    assert m.ev_to_ebit == pytest.approx(3200.0 / 250.0)
    assert m.price_to_earnings == pytest.approx(3000.0 / 150.0)
    assert m.price_to_book == pytest.approx(3000.0 / 1500.0)


@pytest.mark.parametrize(
    "field, multiple",
    [
        ("revenue", "ev_to_revenue"),
        ("ebitda", "ev_to_ebitda"),
        ("ebit", "ev_to_ebit"),
        ("net_income", "price_to_earnings"),
        ("book_value_equity", "price_to_book"),
    ],
)
@pytest.mark.parametrize("value", [-50.0, -0.0001, 0.0], ids=["negative", "barely-negative", "zero"])
def test_non_positive_denominator_gives_no_multiple(field, multiple, value):
    m = compute_peer_multiples(_peer(**{field: value}))
    assert getattr(m, multiple) is None
    # the other multiples are untouched
    others = {"ev_to_revenue", "ev_to_ebitda", "ev_to_ebit", "price_to_earnings", "price_to_book"} - {multiple}
    assert all(getattr(m, name) is not None for name in others)


def test_negative_ebitda_does_not_feed_the_stats():
    peers = [_peer(name="A", ebitda=300.0), _peer(name="B", ebitda=200.0), _peer(name="Loss", ebitda=-50.0)]
    stats = compute_multiple_stats([compute_peer_multiples(p) for p in peers], "ev_to_ebitda")

    a, b = 3200.0 / 300.0, 3200.0 / 200.0
    assert stats.min == pytest.approx(a)  # not -64 (3200 / -50)
    assert stats.max == pytest.approx(b)
    assert stats.median == pytest.approx((a + b) / 2)
    assert stats.min > 0


def test_negative_earnings_does_not_feed_the_stats():
    peers = [_peer(name="A", net_income=150.0), _peer(name="B", net_income=100.0), _peer(name="Loss", net_income=-80.0)]
    stats = compute_multiple_stats([compute_peer_multiples(p) for p in peers], "price_to_earnings")
    assert stats.min == pytest.approx(3000.0 / 150.0)
    assert stats.max == pytest.approx(3000.0 / 100.0)


def test_a_metric_where_every_peer_is_non_positive_has_no_stats_and_does_not_crash():
    peers = [_peer(name="A", ebitda=-10.0), _peer(name="B", ebitda=-20.0)]
    result = run_comps(CompsInput(**comps_inputs(peers=[p.model_dump() for p in peers])))
    assert "ev_to_ebitda" not in {s.metric for s in result.stats}
    assert "ev_to_revenue" in {s.metric for s in result.stats}


def test_comps_endpoint_excludes_the_loss_making_peer_from_the_median():
    loss = {
        "name": "Loss", "revenue": 1000.0, "ebitda": -50.0, "ebit": -60.0, "net_income": -80.0,
        "market_cap": 3000.0, "debt": 300.0, "cash": 100.0,
    }
    body = comps_inputs()
    body["peers"] = body["peers"] + [loss]

    resp = client.post(COMPS, json=body)

    assert resp.status_code == 200, resp.text
    result = resp.json()
    loss_multiples = next(pm for pm in result["peer_multiples"] if pm["name"] == "Loss")
    assert loss_multiples["ev_to_ebitda"] is None
    assert loss_multiples["ev_to_ebit"] is None
    assert loss_multiples["price_to_earnings"] is None
    assert loss_multiples["ev_to_revenue"] is not None  # revenue is positive
    ebitda_stats = next(s for s in result["stats"] if s["metric"] == "ev_to_ebitda")
    assert ebitda_stats["min"] > 0


@pytest.mark.parametrize("token", ["NaN", "Infinity"])
def test_comps_non_finite_peer_number_is_422_not_a_500(token):
    body = comps_inputs()
    body["peers"][0]["ebitda"] = "@@NF@@"
    resp = _post_non_finite(COMPS, body, token)
    assert resp.status_code == 422, resp.text
