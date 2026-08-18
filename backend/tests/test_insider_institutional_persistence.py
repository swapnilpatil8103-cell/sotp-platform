"""Unit tests for the InsiderTransaction / InstitutionalHolding upsert
helpers (SQLite test DB)."""

from __future__ import annotations

from datetime import date

from sqlmodel import select

from backend.data.persistence import (
    get_or_create_company,
    upsert_insider_transactions,
    upsert_institutional_holdings,
)
from backend.models.enums import DataStatus
from backend.models.insider_transaction import InsiderTransaction
from backend.models.institutional_holding import InstitutionalHolding


def test_upsert_insider_transactions_no_duplicates_on_repeat_ingest(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")

    txn1 = InsiderTransaction(
        company_id=company.id,
        reporting_owner_name="Newstead Jennifer",
        reporting_owner_cik="0001780525",
        is_officer=True,
        officer_title="SVP, GC and Secretary",
        transaction_table="nonDerivative",
        security_title="Common Stock",
        transaction_date=date(2026, 8, 11),
        transaction_code="S",
        shares_transacted=1439.0,
        price_per_share=307.75,
        shares_owned_after=40107.0,
        ownership_type="D",
        accession_number="0001140361-26-032884",
        data_status=DataStatus.REPORTED,
    )
    persisted1 = upsert_insider_transactions(session, company.id, [txn1])
    assert len(persisted1) == 1

    # Re-ingest same natural key with an updated shares-owned-after value.
    txn2 = InsiderTransaction(
        company_id=company.id,
        reporting_owner_name="Newstead Jennifer",
        reporting_owner_cik="0001780525",
        transaction_table="nonDerivative",
        security_title="Common Stock",
        transaction_date=date(2026, 8, 11),
        transaction_code="S",
        shares_transacted=1439.0,
        price_per_share=307.75,
        shares_owned_after=99999.0,
        ownership_type="D",
        accession_number="0001140361-26-032884",
        data_status=DataStatus.REPORTED,
    )
    persisted2 = upsert_insider_transactions(session, company.id, [txn2])
    assert len(persisted2) == 1
    assert persisted2[0].id == persisted1[0].id
    assert persisted2[0].shares_owned_after == 99999.0

    all_rows = session.exec(select(InsiderTransaction)).all()
    assert len(all_rows) == 1


def test_upsert_insider_transactions_missing_fields_kept_as_missing(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    txn = InsiderTransaction(
        company_id=company.id,
        transaction_table="derivative",
        security_title="Restricted Stock Unit",
        transaction_date=date(2026, 1, 1),
        accession_number="0000000000-26-000001",
        price_per_share=None,  # genuinely absent in some derivative rows
        data_status=DataStatus.MISSING,
    )
    persisted = upsert_insider_transactions(session, company.id, [txn])
    assert persisted[0].price_per_share is None
    assert persisted[0].data_status == DataStatus.MISSING


def test_upsert_institutional_holdings_no_duplicates_on_repeat_ingest(session):
    holding1 = InstitutionalHolding(
        filer_cik="0001067983",
        filer_name="Berkshire Hathaway Inc",
        issuer_name="ALLY FINL INC",
        cusip="02005N100",
        period_of_report=date(2026, 6, 30),
        value=577211815.0,
        shares_or_principal_amount=12561737.0,
        shares_or_principal_type="SH",
        investment_discretion="DFND",
        voting_authority_sole=12561737.0,
        voting_authority_shared=0.0,
        voting_authority_none=0.0,
        accession_number="0001193125-26-352200",
        data_status=DataStatus.REPORTED,
    )
    persisted1 = upsert_institutional_holdings(session, [holding1])
    assert len(persisted1) == 1

    holding2 = InstitutionalHolding(
        filer_cik="0001067983",
        filer_name="Berkshire Hathaway Inc",
        issuer_name="ALLY FINL INC",
        cusip="02005N100",
        period_of_report=date(2026, 6, 30),
        value=600000000.0,  # updated value on re-ingest
        shares_or_principal_amount=12561737.0,
        shares_or_principal_type="SH",
        investment_discretion="DFND",
        accession_number="0001193125-26-352200",
        data_status=DataStatus.REPORTED,
    )
    persisted2 = upsert_institutional_holdings(session, [holding2])
    assert len(persisted2) == 1
    assert persisted2[0].id == persisted1[0].id
    assert persisted2[0].value == 600000000.0

    all_rows = session.exec(select(InstitutionalHolding)).all()
    assert len(all_rows) == 1


def test_upsert_institutional_holdings_distinguishes_by_cusip(session):
    holdings = [
        InstitutionalHolding(
            filer_cik="0001067983",
            issuer_name="ALLY FINL INC",
            cusip="02005N100",
            accession_number="0001193125-26-352200",
            data_status=DataStatus.REPORTED,
        ),
        InstitutionalHolding(
            filer_cik="0001067983",
            issuer_name="AMERICAN EXPRESS CO",
            cusip="025816109",
            accession_number="0001193125-26-352200",
            data_status=DataStatus.REPORTED,
        ),
    ]
    persisted = upsert_institutional_holdings(session, holdings)
    assert len(persisted) == 2
    assert {h.cusip for h in persisted} == {"02005N100", "025816109"}
