"""Parser for SEC inline-XBRL (iXBRL) primary filing documents.

SEC's XBRL "companyfacts" JSON API only exposes consolidated (non-dimensional)
facts. Segment-level disclosures (revenue/operating income by reporting
segment) are tagged with XBRL dimensions (e.g.
``us-gaap:StatementBusinessSegmentsAxis``) that only appear in the filing's
actual XBRL instance document -- which, since ~2019, SEC filers embed as
*inline XBRL* directly inside the primary 10-K/10-Q HTML document (``ix:*``
tags wrapping ``xbrli:context`` / ``xbrldi:explicitMember`` dimensional
qualifiers).

This module fetches and parses that inline-XBRL document to recover
dimensionally-qualified facts, so ``segment_extractor.py`` can build real
segment breakdowns instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import httpx
from lxml import etree

IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


@dataclass
class XbrlContext:
    context_id: str
    start: Optional[date]
    end: Optional[date]
    instant: Optional[date]
    dimensions: list[tuple[str, str]]  # [(axis_qname, member_qname), ...]


@dataclass
class XbrlFact:
    name: str  # e.g. "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    context_id: str
    value: Optional[float]
    unit_ref: Optional[str]


def _parse_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    try:
        return datetime.strptime(text.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _local(qname: Optional[str]) -> str:
    return (qname or "").split(":")[-1]


def fetch_inline_xbrl_document(url: str, user_agent: str, client: Optional[httpx.Client] = None) -> bytes:
    """Fetch a filing's primary document (inline-XBRL HTML) as raw bytes."""
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    if client is not None:
        resp = client.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    else:
        resp = httpx.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def parse_contexts(root: etree._Element) -> dict[str, XbrlContext]:
    contexts: dict[str, XbrlContext] = {}
    for ctx_el in root.findall(f".//{{{XBRLI_NS}}}context"):
        ctx_id = ctx_el.get("id")
        if not ctx_id:
            continue
        period_el = ctx_el.find(f".//{{{XBRLI_NS}}}period")
        start = end = instant = None
        if period_el is not None:
            start_el = period_el.find(f"{{{XBRLI_NS}}}startDate")
            end_el = period_el.find(f"{{{XBRLI_NS}}}endDate")
            instant_el = period_el.find(f"{{{XBRLI_NS}}}instant")
            start = _parse_date(start_el.text if start_el is not None else None)
            end = _parse_date(end_el.text if end_el is not None else None)
            instant = _parse_date(instant_el.text if instant_el is not None else None)

        dims: list[tuple[str, str]] = []
        for member_el in ctx_el.findall(f".//{{{XBRLDI_NS}}}explicitMember"):
            axis = member_el.get("dimension") or ""
            member = (member_el.text or "").strip()
            if axis and member:
                dims.append((axis, member))

        contexts[ctx_id] = XbrlContext(context_id=ctx_id, start=start, end=end, instant=instant, dimensions=dims)
    return contexts


def _parse_ix_numeric_text(el: etree._Element) -> Optional[float]:
    text = "".join(el.itertext()).strip()
    if not text or text in ("-", "—"):
        return None
    negative_paren = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None

    sign_attr = el.get("sign")
    if sign_attr == "-" or negative_paren:
        value = -value

    scale = el.get("scale")
    if scale is not None:
        try:
            value *= 10 ** int(scale)
        except ValueError:
            pass

    return value


def parse_facts(root: etree._Element) -> list[XbrlFact]:
    facts: list[XbrlFact] = []
    for el in root.findall(f".//{{{IX_NS}}}nonFraction"):
        name = el.get("name") or ""
        context_id = el.get("contextRef") or ""
        if not name or not context_id:
            continue
        facts.append(
            XbrlFact(
                name=name,
                context_id=context_id,
                value=_parse_ix_numeric_text(el),
                unit_ref=el.get("unitRef"),
            )
        )
    return facts


def parse_inline_xbrl(document: bytes) -> tuple[dict[str, XbrlContext], list[XbrlFact]]:
    """Parse an inline-XBRL primary filing document into contexts + facts."""
    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.fromstring(document, parser=parser)
    contexts = parse_contexts(root)
    facts = parse_facts(root)
    return contexts, facts
