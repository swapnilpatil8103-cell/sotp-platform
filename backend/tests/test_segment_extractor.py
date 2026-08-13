"""Unit tests for segment extraction using a hand-crafted inline-XBRL fixture
(no network access)."""

from __future__ import annotations

from backend.data.segment_extractor import (
    SEGMENT_CONCEPTS,
    extract_segments_from_instance,
)
from backend.data.xbrl_instance import parse_inline_xbrl
from backend.models.enums import DataStatus
from backend.tests.fixtures.segment_instance_sample import SAMPLE_INLINE_XBRL


def test_extract_segments_from_fixture_instance():
    contexts, facts = parse_inline_xbrl(SAMPLE_INLINE_XBRL)
    result = extract_segments_from_instance(
        contexts, facts, fiscal_year=2023, fiscal_period="FY", cik10="0000000001", accession_number="acc-1"
    )
    assert result.note is None
    names = sorted(s.name for s in result.segments)
    assert names == ["Alpha Segment", "Beta Segment"]

    alpha = next(s for s in result.segments if s.name == "Alpha Segment")
    facts_by_concept = {f.concept: f for f in alpha.facts}
    assert set(facts_by_concept) == set(SEGMENT_CONCEPTS)
    assert facts_by_concept["revenue"].value == 1000.0
    assert facts_by_concept["revenue"].data_status == DataStatus.REPORTED
    assert facts_by_concept["operating_income"].value == 200.0
    # Undisclosed metrics must come back MISSING, never fabricated.
    assert facts_by_concept["da"].value is None
    assert facts_by_concept["da"].data_status == DataStatus.MISSING
    assert facts_by_concept["capex"].data_status == DataStatus.MISSING
    assert facts_by_concept["assets"].data_status == DataStatus.MISSING

    beta = next(s for s in result.segments if s.name == "Beta Segment")
    beta_by_concept = {f.concept: f for f in beta.facts}
    assert beta_by_concept["operating_income"].value == -50.0


def test_extract_segments_returns_empty_with_note_when_no_dimensional_facts():
    no_segment_doc = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:us-gaap="http://fasb.org/us-gaap/2023">
<body>
<xbrli:context id="c-1">
  <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000002</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period>
</xbrli:context>
<ix:nonFraction unitRef="usd" contextRef="c-1" name="us-gaap:Revenues">1,000</ix:nonFraction>
</body>
</html>
"""
    from backend.data.xbrl_instance import parse_inline_xbrl as parse2

    contexts, facts = parse2(no_segment_doc)
    result = extract_segments_from_instance(contexts, facts, fiscal_year=2023, fiscal_period="FY")
    assert result.segments == []
    assert result.note is not None
    assert "single-segment" in result.note or "No segment" in result.note
