"""POST /valuation/run must never persist a result the server did not compute.

Covers: client-supplied outputs are refused, persisted outputs are the
engine's, approved assumption values must match the inputs actually used,
unknown/smuggled input keys are refused, the existing approval gate still
holds (and still comes first), and the happy path for DCF, COMPS and SOTP.
Every rejection must leave the database untouched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.audit.logger import ACTION_VALUATION_RUN_EXECUTED, get_audit_trail
from backend.data.persistence import get_or_create_company
from backend.governance import approval
from backend.governance.valuation_run import execute_valuation_run, list_versions
from backend.models.enums import ValuationMethod
from backend.schemas.valuation import CompsInput, DcfInput, SotpInput
from backend.tests.fixtures.auth import auth_headers
from backend.tests.fixtures.run_inputs import comps_inputs, dcf_inputs, sotp_inputs
from backend.valuation.comps import run_comps
from backend.valuation.dcf import run_dcf
from backend.valuation.sotp import run_sotp

client = TestClient(app, headers=auth_headers("analyst@firm.com"))

ANALYST = "analyst@firm.com"


def _company(session, ticker="AAPL", cik10="0000320193"):
    return get_or_create_company(session, ticker=ticker, cik10=cik10, name="Apple Inc.")


def _approved(session, company_id, key, value, *, edit_to=None):
    """A decision a human APPROVED as-is, or EDITed to ``edit_to``."""
    decision = approval.propose(
        session, company_id=company_id, assumption_key=key, ai_recommended_value=value, ai_rationale="x"
    )
    if edit_to is None:
        approval.record_decision(session, decision_id=decision.id, decision="APPROVE", user_id=ANALYST)
    else:
        approval.record_decision(
            session,
            decision_id=decision.id,
            decision="EDIT",
            user_id=ANALYST,
            human_value=edit_to,
            reason="human override",
        )
    return decision


def _post_run(company_id, method, decision_ids, inputs, **extra):
    body = {
        "company_id": company_id,
        "method": method,
        "decision_ids": decision_ids,
        "inputs": inputs,
        **extra,
    }
    return client.post("/valuation/run", json=body)


def _assert_nothing_persisted(session, company_id, decisions=()):
    assert list_versions(session, company_id) == []
    trail = get_audit_trail(session, company_id=company_id)
    assert ACTION_VALUATION_RUN_EXECUTED not in [e["action"] for e in trail]
    for decision in decisions:
        session.refresh(decision)
        assert decision.valuation_run_id is None


# --------------------------------------------------------------------------
# Forged outputs
# --------------------------------------------------------------------------


def test_client_supplied_outputs_are_rejected_and_nothing_is_persisted(session):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)

    resp = _post_run(
        company.id,
        "DCF",
        [decision.id],
        dcf_inputs(wacc=0.09),
        outputs={"enterprise_value": 9e18, "equity_value": 9e18, "implied_price_per_share": 9e16},
    )

    assert resp.status_code == 422
    assert "outputs" in resp.text
    _assert_nothing_persisted(session, company.id, [decision])


def test_execute_valuation_run_cannot_be_handed_outputs(session):
    """Structural guard: the function has no outputs parameter, so no caller
    (not just the HTTP layer) can persist an unverified result."""
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)

    with pytest.raises(TypeError):
        execute_valuation_run(  # type: ignore[call-arg]
            session,
            company_id=company.id,
            method=ValuationMethod.DCF,
            decision_ids=[decision.id],
            inputs=dcf_inputs(),
            outputs={"enterprise_value": 9e18},
            created_by=ANALYST,
        )
    _assert_nothing_persisted(session, company.id, [decision])


def test_persisted_outputs_are_the_engines_not_the_clients(session):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)
    inputs = dcf_inputs(wacc=0.09)

    resp = _post_run(company.id, "DCF", [decision.id], inputs)
    assert resp.status_code == 200, resp.text

    expected = run_dcf(DcfInput(**inputs)).model_dump(mode="json", exclude={"inputs"})
    assert resp.json()["outputs"] == expected

    stored = list_versions(session, company.id, ValuationMethod.DCF)[0]
    assert stored.outputs == expected
    assert stored.outputs["enterprise_value"] == pytest.approx(
        run_dcf(DcfInput(**inputs)).enterprise_value
    )


@pytest.mark.parametrize(
    "extra_inputs",
    [
        {"enterprise_value": 9e18},  # a result smuggled into the input snapshot
        {"_governance_snapshot": {"assumptions_used": {"wacc": 0.01}}},  # a forged audit snapshot
        {"exit_mutiple": 12.0},  # a typo'd optional field that would otherwise silently drop out
    ],
)
def test_unknown_input_keys_are_rejected_not_silently_dropped(session, extra_inputs):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)

    resp = _post_run(company.id, "DCF", [decision.id], {**dcf_inputs(wacc=0.09), **extra_inputs})

    assert resp.status_code == 400
    assert "Unknown input field" in resp.json()["detail"]
    _assert_nothing_persisted(session, company.id, [decision])


# --------------------------------------------------------------------------
# Approved assumption values must match the inputs
# --------------------------------------------------------------------------


def test_scalar_assumption_mismatch_is_rejected(session):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)

    resp = _post_run(company.id, "DCF", [decision.id], dcf_inputs(wacc=0.12))

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "inputs.wacc" in detail and "approved_value=0.09" in detail
    _assert_nothing_persisted(session, company.id, [decision])


def test_schedule_assumption_must_hold_in_every_year(session):
    company = _company(session)
    decision = _approved(session, company.id, "revenue_growth_rate", 0.08)

    # year 1 differs from the approved flat 8%
    mismatched = _post_run(company.id, "DCF", [decision.id], dcf_inputs(revenue_growth_rates=[0.10, 0.08]))
    assert mismatched.status_code == 400
    assert "revenue_growth_rates" in mismatched.json()["detail"]
    _assert_nothing_persisted(session, company.id, [decision])

    matched = _post_run(company.id, "DCF", [decision.id], dcf_inputs(revenue_growth_rates=[0.08, 0.08]))
    assert matched.status_code == 200, matched.text


def test_human_edited_value_not_the_ai_value_is_authoritative(session):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.10, edit_to=0.11)

    ai_value = _post_run(company.id, "DCF", [decision.id], dcf_inputs(wacc=0.10))
    assert ai_value.status_code == 400
    _assert_nothing_persisted(session, company.id, [decision])

    human_value = _post_run(company.id, "DCF", [decision.id], dcf_inputs(wacc=0.11))
    assert human_value.status_code == 200, human_value.text


def test_comps_peer_multiple_mismatch_is_rejected(session):
    company = _company(session)
    decision = _approved(session, company.id, "peer_multiple", 11.0)

    resp = _post_run(company.id, "COMPS", [decision.id], comps_inputs(chosen_multiple_value=14.0))

    assert resp.status_code == 400
    assert "chosen_multiple_value" in resp.json()["detail"]
    _assert_nothing_persisted(session, company.id, [decision])


def test_two_decisions_on_one_key_with_different_values_cannot_both_match(session):
    company = _company(session)
    d1 = _approved(session, company.id, "wacc", 0.09)
    d2 = _approved(session, company.id, "wacc", 0.11)

    for wacc in (0.09, 0.11):
        resp = _post_run(company.id, "DCF", [d1.id, d2.id], dcf_inputs(wacc=wacc))
        assert resp.status_code == 400
    _assert_nothing_persisted(session, company.id, [d1, d2])


def test_unmapped_keys_pass_the_gate_and_are_recorded_as_unverified(session):
    company = _company(session)
    wacc = _approved(session, company.id, "wacc", 0.09)
    other = _approved(session, company.id, "value_unlock_action:spin_off", 0.0)

    resp = _post_run(company.id, "DCF", [wacc.id, other.id], dcf_inputs(wacc=0.09))

    assert resp.status_code == 200, resp.text
    snapshot = resp.json()["inputs"]["_governance_snapshot"]
    assert snapshot["assumptions_verified"] == {"wacc": "wacc"}
    assert snapshot["assumptions_unmapped"] == ["value_unlock_action:spin_off"]


# --------------------------------------------------------------------------
# The existing approval gate still holds, and still comes first
# --------------------------------------------------------------------------


def test_pending_decision_is_still_rejected(session):
    company = _company(session)
    pending = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )

    resp = _post_run(company.id, "DCF", [pending.id], dcf_inputs(wacc=0.09))

    assert resp.status_code == 400
    assert "must APPROVE or EDIT" in resp.json()["detail"]
    _assert_nothing_persisted(session, company.id, [pending])


def test_rejected_decision_is_still_rejected(session):
    company = _company(session)
    decision = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )
    approval.record_decision(session, decision_id=decision.id, decision="REJECT", user_id=ANALYST)

    resp = _post_run(company.id, "DCF", [decision.id], dcf_inputs(wacc=0.09))

    assert resp.status_code == 400
    _assert_nothing_persisted(session, company.id, [decision])


def test_another_companys_approved_decision_is_still_rejected(session):
    company_a = _company(session, ticker="AAPL")
    company_b = _company(session, ticker="MSFT", cik10="0000789019")
    decision = _approved(session, company_a.id, "wacc", 0.09)

    resp = _post_run(company_b.id, "DCF", [decision.id], dcf_inputs(wacc=0.09))

    assert resp.status_code == 400
    assert "belongs to company_id" in resp.json()["detail"]
    _assert_nothing_persisted(session, company_b.id)


def test_approval_error_takes_precedence_over_input_errors(session):
    """An unapproved run is refused before any input is even looked at."""
    company = _company(session)
    pending = approval.propose(
        session, company_id=company.id, assumption_key="wacc", ai_recommended_value=0.09, ai_rationale="x"
    )

    resp = _post_run(company.id, "DCF", [pending.id], {"garbage": 1})

    assert resp.status_code == 400
    assert "AssumptionDecision" in resp.json()["detail"]
    assert "Unknown input" not in resp.json()["detail"]


# --------------------------------------------------------------------------
# Invalid inputs
# --------------------------------------------------------------------------


def test_inputs_missing_required_fields_are_rejected(session):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)
    inputs = dcf_inputs(wacc=0.09)
    del inputs["base_revenue"]

    resp = _post_run(company.id, "DCF", [decision.id], inputs)

    assert resp.status_code == 400
    assert "DcfInput" in resp.json()["detail"] and "base_revenue" in resp.json()["detail"]
    _assert_nothing_persisted(session, company.id, [decision])


@pytest.mark.parametrize(
    "overrides, expected_fragment",
    [
        ({"wacc": 0.02, "terminal_growth_rate": 0.03}, "engine rejected"),  # WACC must exceed g
        ({"ebit_margins": [0.20]}, "engine rejected"),  # schedule length mismatch
    ],
)
def test_inputs_the_engine_rejects_are_a_400_not_a_500(session, overrides, expected_fragment):
    company = _company(session)
    decision = _approved(session, company.id, "revenue_growth_rate", 0.10)

    resp = _post_run(
        company.id, "DCF", [decision.id], dcf_inputs(revenue_growth_rates=[0.10, 0.10], **overrides)
    )

    assert resp.status_code == 400
    assert expected_fragment in resp.json()["detail"]
    _assert_nothing_persisted(session, company.id, [decision])


def test_unknown_method_is_a_400(session):
    company = _company(session)
    decision = _approved(session, company.id, "wacc", 0.09)

    resp = _post_run(company.id, "LBO", [decision.id], dcf_inputs())

    assert resp.status_code == 400
    _assert_nothing_persisted(session, company.id, [decision])


# --------------------------------------------------------------------------
# Happy path, all three methods
# --------------------------------------------------------------------------

_HAPPY_CASES = [
    pytest.param("DCF", dcf_inputs(wacc=0.09), "wacc", 0.09, DcfInput, run_dcf, {"wacc": "wacc"}, id="dcf"),
    pytest.param(
        "COMPS",
        comps_inputs(chosen_multiple_value=11.0),
        "peer_multiple",
        11.0,
        CompsInput,
        run_comps,
        {"peer_multiple": "chosen_multiple_value"},
        id="comps",
    ),
    # No AI-proposable key maps onto a SotpInput field, so the decision here
    # clears the approval gate but is recorded as unverified.
    pytest.param("SOTP", sotp_inputs(), "wacc", 0.09, SotpInput, run_sotp, {}, id="sotp"),
]


@pytest.mark.parametrize("method, inputs, key, value, input_model, engine, expected_verified", _HAPPY_CASES)
def test_happy_path_persists_server_computed_run(
    session, method, inputs, key, value, input_model, engine, expected_verified
):
    company = _company(session)
    decision = _approved(session, company.id, key, value)

    resp = _post_run(company.id, method, [decision.id], inputs, source_data_version={"filing": "10-K FY2025"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["method"] == method
    assert body["version"] == 1
    assert body["created_by"] == ANALYST

    expected_outputs = engine(input_model(**inputs)).model_dump(mode="json", exclude={"inputs"})
    assert body["outputs"] == expected_outputs

    snapshot = body["inputs"].pop("_governance_snapshot")
    assert body["inputs"] == input_model(**inputs).model_dump(mode="json")
    assert snapshot["assumption_decision_ids"] == [decision.id]
    assert snapshot["assumptions_used"] == {key: value}
    assert snapshot["assumptions_verified"] == expected_verified
    assert snapshot["assumptions_unmapped"] == ([] if expected_verified else [key])
    assert snapshot["source_data_version"] == {"filing": "10-K FY2025"}

    session.refresh(decision)
    assert decision.valuation_run_id == body["id"]
    trail = get_audit_trail(session, company_id=company.id)
    assert ACTION_VALUATION_RUN_EXECUTED in [e["action"] for e in trail]

    second = _post_run(company.id, method, [decision.id], inputs)
    assert second.status_code == 200
    assert second.json()["version"] == 2
