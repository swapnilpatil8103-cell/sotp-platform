"""Unit tests for backend.data.ownership_xml parsers, against real (Form 4)
and real-trimmed (13F) SEC XML fixtures -- see
backend/tests/fixtures/ownership_samples.py for provenance."""

from __future__ import annotations

from backend.data.ownership_xml import (
    parse_13f_cover_page,
    parse_13f_information_table,
    parse_ownership_document,
)
from backend.tests.fixtures.ownership_samples import (
    SAMPLE_13F_COVER_PAGE_XML,
    SAMPLE_13F_INFO_TABLE_XML,
    SAMPLE_FORM4_XML,
)


def test_parse_ownership_document_real_form4():
    doc = parse_ownership_document(SAMPLE_FORM4_XML)

    assert doc.document_type == "4"
    assert doc.period_of_report == "2026-08-11"
    assert doc.issuer_cik == "0000320193"
    assert doc.issuer_name == "Apple Inc."
    assert doc.issuer_trading_symbol == "AAPL"

    assert doc.reporting_owner_cik == "0001780525"
    assert doc.reporting_owner_name == "Newstead Jennifer"
    assert doc.is_officer is True
    assert doc.officer_title == "SVP, GC and Secretary"
    # Not a director/10% owner per this filing -- must come back None, not False-by-guess.
    assert doc.is_director is None
    assert doc.is_ten_percent_owner is None

    assert len(doc.transactions) == 1
    txn = doc.transactions[0]
    assert txn.table == "nonDerivative"
    assert txn.security_title == "Common Stock"
    assert txn.transaction_date == "2026-08-11"
    assert txn.transaction_code == "S"
    assert txn.transaction_shares == 1439.0
    assert txn.transaction_price_per_share == 307.75
    assert txn.transaction_acquired_disposed_code == "D"
    assert txn.shares_owned_following_transaction == 40107.0
    assert txn.ownership_type == "D"


def test_parse_ownership_document_no_derivative_table_is_empty_not_fabricated():
    doc = parse_ownership_document(SAMPLE_FORM4_XML)
    assert all(t.table != "derivative" for t in doc.transactions)


def test_parse_13f_information_table_real_trimmed():
    holdings = parse_13f_information_table(SAMPLE_13F_INFO_TABLE_XML)
    assert len(holdings) == 2

    first = holdings[0]
    assert first.name_of_issuer == "ALLY FINL INC"
    assert first.cusip == "02005N100"
    assert first.value == 577211815.0
    assert first.shares_or_principal_amount == 12561737.0
    assert first.shares_or_principal_type == "SH"
    assert first.investment_discretion == "DFND"
    assert first.voting_authority_sole == 12561737.0
    assert first.voting_authority_shared == 0.0
    assert first.voting_authority_none == 0.0

    second = holdings[1]
    assert second.name_of_issuer == "AMERICAN EXPRESS CO"
    assert second.cusip == "025816109"


def test_parse_13f_cover_page_real():
    cover = parse_13f_cover_page(SAMPLE_13F_COVER_PAGE_XML)
    assert cover.period_of_report == "06-30-2026"
    assert cover.filing_manager_name == "Berkshire Hathaway Inc"
    assert cover.report_type == "13F HOLDINGS REPORT"
