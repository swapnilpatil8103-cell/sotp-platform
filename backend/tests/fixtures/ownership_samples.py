"""Real (trimmed) SEC ownership XML fixtures used by
`test_ownership_xml.py` / `test_sec_connector_ownership_methods.py`.

SAMPLE_FORM4_XML is a real Apple Inc. Form 4 (issuer CIK 0000320193,
accession 0001140361-26-032884, reporting owner Jennifer Newstead, SVP/GC),
fetched directly from
https://www.sec.gov/Archives/edgar/data/320193/000114036126032884/form4.xml
and reproduced verbatim (single non-derivative sale transaction, no
derivative table -- a real example of a Form 4 with no derivative rows at
all).

SAMPLE_13F_INFO_TABLE_XML is a trimmed excerpt (first 2 positions) of a real
Berkshire Hathaway Inc 13F-HR information table (filer CIK 0001067983,
accession 0001193125-26-352200, period of report 2026-06-30), fetched from
https://www.sec.gov/Archives/edgar/data/1067983/000119312526352200/56757.xml
-- the full document has hundreds of <infoTable> rows; only the shape
matters for parser testing.

SAMPLE_13F_COVER_PAGE_XML is a trimmed excerpt of that same filing's
primary_doc.xml cover page (filer identity + period of report), fetched from
https://www.sec.gov/Archives/edgar/data/1067983/000119312526352200/primary_doc.xml.
"""

SAMPLE_FORM4_XML = b"""<?xml version="1.0"?>
<ownershipDocument>

    <schemaVersion>X0609</schemaVersion>

    <documentType>4</documentType>

    <periodOfReport>2026-08-11</periodOfReport>

    <issuer>
        <issuerCik>0000320193</issuerCik>
        <issuerName>Apple Inc.</issuerName>
        <issuerTradingSymbol>AAPL</issuerTradingSymbol>
        <issuerForeignTradingSymbol></issuerForeignTradingSymbol>
    </issuer>

    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001780525</rptOwnerCik>
            <rptOwnerName>Newstead Jennifer</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerAddress>
            <rptOwnerNonUSAddressFlag>false</rptOwnerNonUSAddressFlag>
            <rptOwnerStreet1>ONE APPLE PARK WAY</rptOwnerStreet1>
            <rptOwnerStreet2></rptOwnerStreet2>
            <rptOwnerCity>CUPERTINO</rptOwnerCity>
            <rptOwnerState>CA</rptOwnerState>
            <rptOwnerZipCode>95014</rptOwnerZipCode>
            <rptOwnerStateDescription></rptOwnerStateDescription>
        </reportingOwnerAddress>
        <reportingOwnerRelationship>
            <isOfficer>true</isOfficer>
            <officerTitle>SVP, GC and Secretary</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>

    <aff10b5One>true</aff10b5One>

    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <securityTitle>
                <value>Common Stock</value>
                <footnoteId id="F1"/>
            </securityTitle>
            <transactionDate>
                <value>2026-08-11</value>
            </transactionDate>
            <transactionCoding>
                <transactionFormType>4</transactionFormType>
                <transactionCode>S</transactionCode>
                <equitySwapInvolved>0</equitySwapInvolved>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>1439</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>307.75</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>40107</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
            <ownershipNature>
                <directOrIndirectOwnership>
                    <value>D</value>
                </directOrIndirectOwnership>
            </ownershipNature>
        </nonDerivativeTransaction>
    </nonDerivativeTable>

    <footnotes>
        <footnote id="F1">This transaction was made pursuant to a Rule 10b5-1 trading plan adopted by the reporting person on May 5, 2026.</footnote>
    </footnotes>

    <ownerSignature>
        <signatureName>/s/ Sam Whittington, Attorney-in-Fact for Jennifer Newstead</signatureName>
        <signatureDate>2026-08-13</signatureDate>
    </ownerSignature>
</ownershipDocument>
"""

SAMPLE_13F_INFO_TABLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ALLY FINL INC</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>02005N100</cusip>
    <value>577211815</value>
    <shrsOrPrnAmt>
      <sshPrnamt>12561737</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>DFND</investmentDiscretion>
    <otherManager>4</otherManager>
    <votingAuthority>
      <Sole>12561737</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
  <infoTable>
    <nameOfIssuer>AMERICAN EXPRESS CO</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>025816109</cusip>
    <value>45123456</value>
    <shrsOrPrnAmt>
      <sshPrnamt>151610700</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
    <votingAuthority>
      <Sole>151610700</Sole>
      <Shared>0</Shared>
      <None>0</None>
    </votingAuthority>
  </infoTable>
</informationTable>
"""

SAMPLE_13F_COVER_PAGE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://www.sec.gov/edgar/thirteenf/primarydoc">
  <headerData>
    <submissionType>13F-HR</submissionType>
    <filerInfo>
      <liveTestFlag>LIVE</liveTestFlag>
      <flags>
        <confidentialTreatmentFlag>N</confidentialTreatmentFlag>
      </flags>
      <periodOfReport>06-30-2026</periodOfReport>
    </filerInfo>
  </headerData>
  <formData>
    <coverPage>
      <reportCalendarOrQuarter>06-30-2026</reportCalendarOrQuarter>
      <filingManager>
        <name>Berkshire Hathaway Inc</name>
        <address>
          <ns1:street1 xmlns:ns1="http://www.sec.gov/edgar/common">3555 FARNAM STREET</ns1:street1>
          <ns1:city xmlns:ns1="http://www.sec.gov/edgar/common">OMAHA</ns1:city>
          <ns1:stateOrCountry xmlns:ns1="http://www.sec.gov/edgar/common">NE</ns1:stateOrCountry>
          <ns1:zipCode xmlns:ns1="http://www.sec.gov/edgar/common">68131</ns1:zipCode>
        </address>
      </filingManager>
      <reportType>13F HOLDINGS REPORT</reportType>
    </coverPage>
  </formData>
</edgarSubmission>
"""
