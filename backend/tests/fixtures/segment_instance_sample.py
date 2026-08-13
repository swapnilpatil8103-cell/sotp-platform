"""Hand-crafted minimal inline-XBRL document fixture for segment extraction unit tests.

Mimics the real shape SEC filers use: an XHTML document with ``xbrli:context``
elements carrying ``xbrldi:explicitMember`` dimensional qualifiers on
``us-gaap:StatementBusinessSegmentsAxis``, and ``ix:nonFraction`` facts
referencing those contexts. Two segments (Alpha, Beta), one fiscal year
(2023), revenue + operating_income reported for both, D&A/capex/assets
undisclosed (should come back MISSING).
"""

SAMPLE_INLINE_XBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2023">
<head><title>Fixture 10-K</title></head>
<body>
<ix:header>
  <ix:resources>
    <xbrli:context id="c-consolidated">
      <xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier></xbrli:entity>
      <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period>
    </xbrli:context>
    <xbrli:context id="c-alpha">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>
        <xbrli:segment>
          <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">fix:AlphaSegmentMember</xbrldi:explicitMember>
        </xbrli:segment>
      </xbrli:entity>
      <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period>
    </xbrli:context>
    <xbrli:context id="c-beta">
      <xbrli:entity>
        <xbrli:identifier scheme="http://www.sec.gov/CIK">0000000001</xbrli:identifier>
        <xbrli:segment>
          <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis">fix:BetaSegmentMember</xbrldi:explicitMember>
        </xbrli:segment>
      </xbrli:entity>
      <xbrli:period><xbrli:startDate>2023-01-01</xbrli:startDate><xbrli:endDate>2023-12-31</xbrli:endDate></xbrli:period>
    </xbrli:context>
  </ix:resources>
</ix:header>
<div style="display:none">
  <span><ix:nonFraction unitRef="usd" contextRef="c-alpha" decimals="-6" name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax">1,000</ix:nonFraction></span>
  <span><ix:nonFraction unitRef="usd" contextRef="c-alpha" decimals="-6" name="us-gaap:OperatingIncomeLoss">200</ix:nonFraction></span>
  <span><ix:nonFraction unitRef="usd" contextRef="c-beta" decimals="-6" name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax">500</ix:nonFraction></span>
  <span><ix:nonFraction unitRef="usd" contextRef="c-beta" decimals="-6" name="us-gaap:OperatingIncomeLoss">(50)</ix:nonFraction></span>
  <span><ix:nonFraction unitRef="usd" contextRef="c-consolidated" decimals="-6" name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax">1,500</ix:nonFraction></span>
</div>
</body>
</html>
"""
