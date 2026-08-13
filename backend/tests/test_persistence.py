"""Unit tests for backend.data.persistence upsert helpers (SQLite test DB)."""

from __future__ import annotations

from backend.data.persistence import (
    get_or_create_company,
    get_or_create_segment,
    upsert_financial_facts,
    upsert_segment_financial_facts,
)
from backend.models.enums import DataStatus
from backend.models.financial_fact import FinancialFact
from backend.models.segment_financial_fact import SegmentFinancialFact


def test_get_or_create_company_is_idempotent(session):
    c1 = get_or_create_company(session, ticker="aapl", cik10="0000320193", name="Apple Inc.")
    c2 = get_or_create_company(session, ticker="AAPL", cik10="0000320193")
    assert c1.id == c2.id


def test_upsert_financial_facts_no_duplicates_on_repeat_ingest(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")

    facts_round1 = [
        FinancialFact(
            company_id=company.id,
            concept="revenue",
            value=100.0,
            period="2023-FY",
            fiscal_year=2023,
            fiscal_period="FY",
            source="SEC XBRL",
            xbrl_tag="Revenues",
            data_status=DataStatus.REPORTED,
        )
    ]
    persisted1 = upsert_financial_facts(session, company.id, facts_round1)
    assert len(persisted1) == 1

    # Re-ingest with an updated value -- should update in place, not duplicate.
    facts_round2 = [
        FinancialFact(
            company_id=company.id,
            concept="revenue",
            value=150.0,
            period="2023-FY",
            fiscal_year=2023,
            fiscal_period="FY",
            source="SEC XBRL",
            xbrl_tag="Revenues",
            data_status=DataStatus.REPORTED,
        )
    ]
    persisted2 = upsert_financial_facts(session, company.id, facts_round2)
    assert len(persisted2) == 1
    assert persisted2[0].id == persisted1[0].id
    assert persisted2[0].value == 150.0

    from sqlmodel import select

    all_rows = session.exec(select(FinancialFact).where(FinancialFact.company_id == company.id)).all()
    assert len(all_rows) == 1


def test_upsert_segment_financial_facts_no_duplicates(session):
    company = get_or_create_company(session, ticker="GOOGL", cik10="0001652044", name="Alphabet Inc.")
    segment = get_or_create_segment(session, company_id=company.id, name="Google Cloud")
    segment2 = get_or_create_segment(session, company_id=company.id, name="Google Cloud")
    assert segment.id == segment2.id

    fact = SegmentFinancialFact(
        segment_id=segment.id,
        concept="revenue",
        value=10.0,
        period="2023-FY",
        fiscal_year=2023,
        fiscal_period="FY",
        source="SEC XBRL",
        xbrl_tag="RevenueFromContractWithCustomerExcludingAssessedTax",
        data_status=DataStatus.REPORTED,
    )
    p1 = upsert_segment_financial_facts(session, segment.id, [fact])
    fact2 = SegmentFinancialFact(
        segment_id=segment.id,
        concept="revenue",
        value=12.0,
        period="2023-FY",
        fiscal_year=2023,
        fiscal_period="FY",
        source="SEC XBRL",
        xbrl_tag="RevenueFromContractWithCustomerExcludingAssessedTax",
        data_status=DataStatus.REPORTED,
    )
    p2 = upsert_segment_financial_facts(session, segment.id, [fact2])
    assert p1[0].id == p2[0].id
    assert p2[0].value == 12.0
