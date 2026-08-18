"""Parsers for SEC Form 3/4/5 ownership documents and 13F information tables.

Both are plain (non-inline) XML documents SEC filers submit directly --
unlike the inline-XBRL instance documents `xbrl_instance.py` handles, these
are not embedded in an HTML wrapper, so parsing is a straightforward
element-tree walk. No XBRL taxonomy/dimension machinery is involved.

Real document shapes were inspected before writing this module (see
`docs/data-model.md` for the summary): a real Apple Inc. Form 4
(CIK 0000320193, accession 0001140361-26-032884) and a real Berkshire
Hathaway 13F-HR (CIK 0001067983, accession 0001193125-26-352200).

Design principle shared with the rest of the codebase: a field that is
genuinely absent from the XML (e.g. a Form 4 with only non-derivative
transactions has no `derivativeTable` at all; a reporting owner who is
merely a director has no `officerTitle`) comes back as `None`/absent --
never guessed, never defaulted to a fabricated value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from lxml import etree


def _text(el: Optional[etree._Element]) -> Optional[str]:
    if el is None:
        return None
    t = el.text
    if t is None:
        return None
    t = t.strip()
    return t or None


def _find_value(parent: Optional[etree._Element], path: str) -> Optional[str]:
    """Many Form 3/4/5 fields are wrapped one level deep: <tag><value>X</value></tag>."""
    if parent is None:
        return None
    node = parent.find(path)
    if node is None:
        return None
    value_node = node.find("value")
    if value_node is not None:
        return _text(value_node)
    return _text(node)


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_bool(s: Optional[str]) -> Optional[bool]:
    if s is None:
        return None
    return s.strip().lower() in ("1", "true")


@dataclass
class OwnershipTransaction:
    """One non-derivative or derivative transaction row from a Form 3/4/5."""

    table: str  # "nonDerivative" | "derivative"
    security_title: Optional[str]
    transaction_date: Optional[str]
    transaction_code: Optional[str]
    transaction_shares: Optional[float]
    transaction_price_per_share: Optional[float]
    transaction_acquired_disposed_code: Optional[str]
    shares_owned_following_transaction: Optional[float]
    ownership_type: Optional[str]  # "D" (direct) | "I" (indirect)


@dataclass
class OwnershipDocument:
    """Parsed Form 3/4/5 ownership document."""

    document_type: Optional[str]
    period_of_report: Optional[str]
    issuer_cik: Optional[str]
    issuer_name: Optional[str]
    issuer_trading_symbol: Optional[str]
    reporting_owner_cik: Optional[str]
    reporting_owner_name: Optional[str]
    is_director: Optional[bool]
    is_officer: Optional[bool]
    is_ten_percent_owner: Optional[bool]
    is_other: Optional[bool]
    officer_title: Optional[str]
    transactions: list[OwnershipTransaction] = field(default_factory=list)


def parse_ownership_document(xml_bytes: bytes) -> OwnershipDocument:
    """Parse a Form 3/4/5 `ownershipDocument` XML into structured data.

    Handles both `nonDerivativeTable`/`nonDerivativeTransaction` (common
    stock, etc.) and `derivativeTable`/`derivativeTransaction` (options,
    RSUs, etc.) rows -- these carry different field sets in real filings,
    so absent fields are left `None` rather than guessed.
    """
    root = etree.fromstring(xml_bytes)

    issuer = root.find("issuer")
    owner = root.find("reportingOwner")
    owner_id = owner.find("reportingOwnerId") if owner is not None else None
    relationship = owner.find("reportingOwnerRelationship") if owner is not None else None

    doc = OwnershipDocument(
        document_type=_text(root.find("documentType")),
        period_of_report=_text(root.find("periodOfReport")),
        issuer_cik=_text(issuer.find("issuerCik")) if issuer is not None else None,
        issuer_name=_text(issuer.find("issuerName")) if issuer is not None else None,
        issuer_trading_symbol=_text(issuer.find("issuerTradingSymbol")) if issuer is not None else None,
        reporting_owner_cik=_text(owner_id.find("rptOwnerCik")) if owner_id is not None else None,
        reporting_owner_name=_text(owner_id.find("rptOwnerName")) if owner_id is not None else None,
        is_director=_to_bool(_text(relationship.find("isDirector"))) if relationship is not None else None,
        is_officer=_to_bool(_text(relationship.find("isOfficer"))) if relationship is not None else None,
        is_ten_percent_owner=_to_bool(_text(relationship.find("isTenPercentOwner"))) if relationship is not None else None,
        is_other=_to_bool(_text(relationship.find("isOther"))) if relationship is not None else None,
        officer_title=_text(relationship.find("officerTitle")) if relationship is not None else None,
    )

    for table_tag, txn_tag, kind in (
        ("nonDerivativeTable", "nonDerivativeTransaction", "nonDerivative"),
        ("derivativeTable", "derivativeTransaction", "derivative"),
    ):
        table = root.find(table_tag)
        if table is None:
            continue
        for txn in table.findall(txn_tag):
            post = txn.find("postTransactionAmounts")
            ownership_nature = txn.find("ownershipNature")
            doc.transactions.append(
                OwnershipTransaction(
                    table=kind,
                    security_title=_find_value(txn, "securityTitle"),
                    transaction_date=_find_value(txn, "transactionDate"),
                    transaction_code=_find_value(txn.find("transactionCoding"), "transactionCode")
                    if txn.find("transactionCoding") is not None
                    else None,
                    transaction_shares=_to_float(_find_value(txn, "transactionAmounts/transactionShares")),
                    transaction_price_per_share=_to_float(
                        _find_value(txn, "transactionAmounts/transactionPricePerShare")
                    ),
                    transaction_acquired_disposed_code=_find_value(
                        txn, "transactionAmounts/transactionAcquiredDisposedCode"
                    ),
                    shares_owned_following_transaction=_to_float(
                        _find_value(post, "sharesOwnedFollowingTransaction") if post is not None else None
                    ),
                    ownership_type=_find_value(ownership_nature, "directOrIndirectOwnership")
                    if ownership_nature is not None
                    else None,
                )
            )

    return doc


@dataclass
class Form13FHolding:
    """One `infoTable` row from a 13F information table."""

    name_of_issuer: Optional[str]
    title_of_class: Optional[str]
    cusip: Optional[str]
    value: Optional[float]  # reported in thousands of USD per SEC convention
    shares_or_principal_amount: Optional[float]
    shares_or_principal_type: Optional[str]  # "SH" | "PRN"
    investment_discretion: Optional[str]
    voting_authority_sole: Optional[float]
    voting_authority_shared: Optional[float]
    voting_authority_none: Optional[float]


_INFO_TABLE_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def parse_13f_information_table(xml_bytes: bytes) -> list[Form13FHolding]:
    """Parse a 13F `informationTable` XML document into structured holdings.

    Real document inspected: Berkshire Hathaway Inc's 13F-HR for the period
    ended 2026-06-30 (CIK 0001067983, accession 0001193125-26-352200,
    info table file `56757.xml`). The root element is namespaced
    (`xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"`);
    `lxml`'s `local-name()` matching below is namespace-agnostic so this
    also tolerates filings submitted without an explicit namespace prefix.
    """
    root = etree.fromstring(xml_bytes)
    holdings: list[Form13FHolding] = []

    for info_table in root.findall(".//{*}infoTable"):

        def g(tag: str) -> Optional[etree._Element]:
            return info_table.find(f"{{*}}{tag}")

        shrs = g("shrsOrPrnAmt")
        voting = g("votingAuthority")

        holdings.append(
            Form13FHolding(
                name_of_issuer=_text(g("nameOfIssuer")),
                title_of_class=_text(g("titleOfClass")),
                cusip=_text(g("cusip")),
                value=_to_float(_text(g("value"))),
                shares_or_principal_amount=_to_float(_text(shrs.find("{*}sshPrnamt"))) if shrs is not None else None,
                shares_or_principal_type=_text(shrs.find("{*}sshPrnamtType")) if shrs is not None else None,
                investment_discretion=_text(g("investmentDiscretion")),
                voting_authority_sole=_to_float(_text(voting.find("{*}Sole"))) if voting is not None else None,
                voting_authority_shared=_to_float(_text(voting.find("{*}Shared"))) if voting is not None else None,
                voting_authority_none=_to_float(_text(voting.find("{*}None"))) if voting is not None else None,
            )
        )

    return holdings


@dataclass
class Form13FCoverPage:
    """Metadata from a 13F's `primary_doc.xml` cover page (filer identity,
    period of report) -- the information table itself carries neither."""

    period_of_report: Optional[str]
    filing_manager_name: Optional[str]
    report_type: Optional[str]


def parse_13f_cover_page(xml_bytes: bytes) -> Form13FCoverPage:
    root = etree.fromstring(xml_bytes)
    filer_info = root.find(".//{*}filerInfo")
    filing_manager = root.find(".//{*}filingManager")
    return Form13FCoverPage(
        period_of_report=_text(filer_info.find("{*}periodOfReport")) if filer_info is not None else None,
        filing_manager_name=_text(filing_manager.find("{*}name")) if filing_manager is not None else None,
        report_type=_text(root.find(".//{*}reportType")),
    )
