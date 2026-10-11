# Data validation report

Verification of the SEC ingestion + normalization pipeline against real filings.
Run on 2026-10-11 against live SEC data (latest 10-K per company), through the HTTP API
(`/companies/{t}`, `/companies/{t}/facts`, `/companies/{t}/segments`).

**Method.** For each figure, "our value" is what the API returned. "SEC value" was obtained
independently of our mapping code by (a) reading the raw `data.sec.gov/api/xbrl/companyfacts`
JSON, keeping only entries of the latest 10-K's accession number and the fiscal-year period, and
(b) finding the same number on the filing's own statement pages (`FilingSummary.xml` → R pages,
the pages the SEC renders from the filing). Where only (a) was possible it says so. Nothing in
this report is estimated; "not verified" means exactly that.

## 1. Integration tests (`pytest -m integration`, live network)

All 12 pass:
`test_aapl_real_multi_year_revenue_history_and_cagr`, `test_full_pipeline_aapl_dcf_and_sotp`,
`test_full_pipeline_jpm_financial_institution_classification`, `test_real_yfinance_lookup_for_aapl`,
`test_real_local_ollama_service`, `test_real_sec_lookup_for_aapl`,
`test_real_get_company_concept_for_aapl`, `test_real_get_filing_document_for_aapl`,
`test_real_get_insider_filings_and_ownership_document_for_aapl`,
`test_real_get_13f_holdings_for_berkshire`, `test_real_sec_frames_revenues_recent_quarter`,
`test_real_googl_segment_extraction`.

One of them failed mid-task: `test_real_googl_segment_extraction` asserted `result.note is None`.
After this work the extractor attaches an informational note (Alphabet's "Corporate Non Segment:
operating_income -16,760M" reconciling item), so the assertion no longer meant "real data was
found". It now asserts that segments are present and that the "No segment-dimensional XBRL facts"
note is absent. This is a change of what the assertion checks, not a skip, and the check for real
segment data remains. The data itself was not changed to satisfy it.

## 2. Consolidated figures, latest 10-K

Amounts in USD millions unless noted. "Filing page" = number located on the filing's own R page;
"CF" = matched in companyfacts for the 10-K's accession.

| Ticker | Metric | Our value | SEC value | Match | Notes |
|---|---|---|---|---|---|
| JPM (FY2025) | Revenue | 182,447 | 182,447 (CF `Revenues` = `RevenuesNetOfInterestExpense`; filing page "Total net revenue") | yes | |
| JPM | Operating income | MISSING | no tag in CF; no such line in a bank income statement | yes (correctly missing) | Banks do not report operating income; not guessed |
| JPM | Net income | 57,048 | 57,048 (CF, filing page) | yes | |
| JPM | Total debt | 435,206 (REPORTED, `LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities`) | filing page "Long-term debt" 435,206; CF also has `ShortTermBorrowings` 64,776 | **partial** | The figure is long-term debt only (includes $134,559M at fair value per the page label); short-term borrowings are excluded, so it is not "total debt" in the sense used for other companies. Open gap, see section 6 |
| JPM | Cash | 343,338 (`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`) | 343,338 (CF) | yes (CF only) | Cash-flow-statement definition including restricted cash; CF also has `CashAndDueFromBanks` 21,742. Not found on saved R pages, so only CF verified |
| JPM | Shares outstanding | 2,697,032,375 | 2,697,032,375 (CF cover page, as of 2026-01-31; filing cover page) | yes | Cover-page date is after fiscal year-end; the date is in `status_note` |
| GE (FY2025) | Revenue | 45,855 | 45,855 (CF, filing page "Total revenue") | yes | |
| GE | Operating income | MISSING | no `OperatingIncomeLoss` in CF | yes (correctly missing) | GE does not tag it |
| GE | Net income | 8,704 | 8,704 (CF `NetIncomeLoss`; filing page "attributable to the Company"); `ProfitLoss` is 8,698 | yes | Attributable-to-parent used |
| GE | Total debt | 20,494 | 20,494 = 18,808 + 1,686 (CF `LongTermDebtAndCapitalLeaseObligations` + `DebtCurrent`); CF also reports the combined tag `DebtLongtermAndShorttermCombinedAmount` | yes (CF) | Total not printed on the balance sheet; the long-term part (18,808) is on the filing page |
| GE | Long-term debt | 18,808 | 18,808 (CF, filing page "Long-term borrowings") | yes | Was wrong before (20,469 = `LongTermDebt`, which includes the current portion) |
| GE | Cash | 12,392 | 12,392 (CF, filing page "Cash, cash equivalents and restricted cash") | yes | |
| GE | Shares outstanding | 1,048,813,612 | 1,048,813,612 (CF, filing cover page; 2026-01-15) | yes | |
| AAPL (FY2025) | Revenue | 416,161 | 416,161 (CF, filing page "Net sales") | yes | Contract-revenue tag; no total tag exists |
| AAPL | Operating income | 133,050 | 133,050 (CF, filing page) | yes | |
| AAPL | Net income | 112,010 | 112,010 (CF, filing page) | yes | |
| AAPL | Total debt | 98,657 (DERIVED) | 7,979 CP + 12,350 current term debt + 78,328 term debt = 98,657 | yes | Components all on the filing page; the sum is ours and labelled DERIVED |
| AAPL | Cash | 35,934 | 35,934 (CF, filing page) | yes | |
| AAPL | Shares outstanding | 14,776,353,000 | 14,776,353,000 (CF cover page, 2025-10-17) | yes (CF) | Filing cover page present but my number check there scaled the units wrongly; CF value used |
| GOOGL (FY2025) | Revenue | 402,836 | 402,836 (CF, filing page) | yes | |
| GOOGL | Operating income | 129,039 | 129,039 (CF, filing page) | yes | |
| GOOGL | Net income | 132,170 | 132,170 (CF, filing page) | yes | |
| GOOGL | Total debt | 48,543 (DERIVED) | 46,547 (filing page) + 1,996 (CF `LongTermDebtCurrent`) = 48,543; CP 0 | yes (CF for 1,996) | Current portion not on the balance sheet page; verified via CF only |
| GOOGL | Cash | 30,708 | 30,708 (CF, filing page) | yes | |
| GOOGL | Shares outstanding | 12,088,000,000 | 12,088,000,000 (filing balance sheet parenthetical, balance-sheet date) | yes, with caveat | Class A+B+C total at the balance-sheet date. The cover-page counts are per class (dimensional) and not in companyfacts |
| INTC (FY2025) | Revenue | 52,853 | 52,853 (CF, filing page) | yes | |
| INTC | Operating income | -2,214 | -2,214 (CF, filing page) | yes | |
| INTC | Net income | -267 | -267 (CF, filing page "attributable to Intel"); `ProfitLoss` is +26 | yes | Attributable-to-parent used |
| INTC | Total debt | 46,585 (DERIVED) | 44,086 + 2,499 = 46,585 (filing page; matches CF `LongTermDebt` 46,585) | yes | |
| INTC | Cash | 14,265 | 14,265 (CF, filing page) | yes | CF's restricted-cash-inclusive figure is 14,712; we use the balance-sheet figure |
| INTC | Shares outstanding | 4,995,000,000 | 4,995,000,000 (CF cover, 2026-01-16; filing page "4,995" in millions) | yes | |
| BRK-B (FY2025) | Revenue | 371,444 | 371,444 (CF `Revenues`; filing page "Total revenues") | yes | CF also has `RevenueFromContractWithCustomer…` 247,244, a subset; the total is used and the note says so |
| BRK-B | Operating income | MISSING | not tagged | yes (correctly missing) | |
| BRK-B | Net income | 66,968 | 66,968 (CF, filing page "attributable to Berkshire shareholders"); `ProfitLoss` 67,260 | yes | |
| BRK-B | Total debt | MISSING | no consolidated debt tag in CF | yes (correctly missing) | Debt is split across insurance/BNSF/BHE notes payable; not summed |
| BRK-B | Cash | 52,569 | 52,569 (CF, restricted-cash-inclusive) | yes (CF only) | Balance sheet shows 47,719 + 4,158 = 51,877 for the two groups, so this differs by restricted cash. Different definition, not a data error; not found on a saved page |
| BRK-B | Shares outstanding | MISSING | CF has no consolidated figure (dual-class A/B, dimensional on the cover) | yes (correctly missing) | |

## 3. Segment revenue and operating income

Each value below was found on a filing segment page (R-page) unless stated. Status is what the API reports.

| Ticker | Segment | Revenue | Operating income | Status | Filing check / notes |
|---|---|---|---|---|---|
| AAPL | Americas | 178,353 | 72,480 | REPORTED | yes |
| AAPL | Europe | 111,032 | 47,739 | REPORTED | yes |
| AAPL | Greater China | 64,377 | 26,917 | REPORTED | yes (filing row "China") |
| AAPL | Japan | 28,703 | 13,955 | REPORTED | yes |
| AAPL | Rest of Asia Pacific | 33,696 | 14,586 | REPORTED | yes |
| GOOGL | Google Services | 342,721 | 139,404 | REPORTED | yes |
| GOOGL | Google Cloud | 58,705 | 13,910 | REPORTED | yes |
| GOOGL | All Other Bets | 1,537 | -7,515 | REPORTED | yes. Corporate costs -16,760 (operating income) are a reconciling item, noted in the response, not a segment |
| GE | Commercial Engines & Services | 33,314 | 8,861 | revenue CONFLICTING | Segment table 33,314 vs 33,252 in another disclosure; table value used. The operating-income number matched a row labelled "Gross profit (loss)" on the filing page, so the label is not confirmed to be segment profit |
| GE | Defense & Propulsion Technologies | 10,554 | 1,296 | revenue CONFLICTING | 10,554 vs 8,868 in another disclosure; table value used. Same label caveat |
| JPM | Consumer & Community Banking | 76,029 | not extracted | REPORTED | yes |
| JPM | Commercial & Investment Bank | 78,454 | not extracted | REPORTED | yes |
| JPM | Asset & Wealth Management | 24,073 | not extracted | REPORTED | yes. Corporate 7,025 and reconciling items -3,134 are noted in the response |
| INTC | Intel Foundry | 17,826 | -10,318 | REPORTED | yes |
| INTC | Client Computing Group | 32,228 | 9,317 | REPORTED, `parent_segment` set | yes; nested under the Intel Products group, do not add to it |
| INTC | Datacenter & AI | 16,919 | 3,422 | REPORTED, `parent_segment` set | yes; nested |
| INTC | Client Computing Group + Datacenter & AI + Network & Edge (group) | 49,147 | 12,739 | REPORTED | yes; 49,147 = 32,228 + 16,919 |
| INTC | All Other | 3,563 | 264 | REPORTED | yes |
| BRK-B | Insurance (Berkshire Hathaway Insurance Group) | 104,212 | n/a | REPORTED | not located on saved pages (unverified) |
| BRK-B | BNSF | 23,533 | n/a | CONFLICTING | total revenue chosen over contract revenue 23,256; other disclosure 23,441; not located on saved pages (unverified) |
| BRK-B | BHE | 26,297 | n/a | CONFLICTING | contract 25,351; other disclosure 26,282; unverified |
| BRK-B | Manufacturing | 78,487 | n/a | CONFLICTING | contract 72,830; other disclosure 78,390; filing page "Revenues" 78,487 |
| BRK-B | Service & Retailing | 42,647 | n/a | CONFLICTING | contract 33,751; other 42,560; unverified |
| BRK-B | McLane | 50,998 | n/a | CONFLICTING | filing page 50,998 |
| BRK-B | Pilot Travel Centers | 42,198 | n/a | CONFLICTING | unverified |

Segment that changed recently: **Intel** (reorganised into Intel Products with CCG and DCAI,
Intel Foundry, and All Other, with Altera, Mobileye and Network & Edge appearing only in other
periods). The pipeline returns only members with a current-period value, lists the omitted ones
in `note`, and flags CCG and DCAI as nested.

## 4. Bugs found and fixed

All have regression tests in `backend/tests/test_data_validation_regressions.py`, built from
trimmed copies of the real companyfacts responses in `backend/tests/fixtures/real_companyfacts/`.

1. **Stale share counts.** The cover-page `EntityCommonStockSharesOutstanding` has `end` equal to
   the cover date (after fiscal year-end). Period matching therefore skipped it for December
   year-ends and used an older balance-sheet-style number for JPM, GE and INTC. Fixed in
   `normalizer.py` (`_pick_shares`); the cover date is shown in `status_note`.
2. **Revenue subset reported as total.** Berkshire's contract-revenue tag (247,244M) is a subset
   of `Revenues` (371,444M). The resolver deduplicated across tags and hid the larger one. Fixed in
   `concept_mapping.py` (`resolve_tags` keeps every tag) and `normalizer.py` (`_prefer_total_revenue`);
   the same rule for segments in `segment_extractor.py`.
3. **Wrong or missing debt.** `LongTermDebt` includes the current portion, so it was used as
   long-term debt, wrong for GE (20,469 vs 18,808) and others; total debt was missing when no total
   tag existed. Fixed: long-term debt candidates are noncurrent tags only; total debt is derived
   from reported components and labelled DERIVED with the components in `status_note`.
4. **Restatements silently overwritten.** GE FY2023 revenue has three different values across
   filings (for example 67,954M and 35,348M). The value is now
   CONFLICTING with a note naming the filings. CAGR code only uses REPORTED years.
5. **Two code paths, different rules.** The multi-year path had its own selection logic. Both now
   call `normalizer.build_fact`.
6. **Bank segments had no revenue.** JPM tags segment revenue `RevenuesNetOfInterestExpense`,
   which was not a candidate. Added.
7. **Conflicting segment disclosures.** The same segment/tag had different values in different
   notes; the first was used silently. Now the reportable-segment table wins and the result is
   CONFLICTING with both values in `status_note`.
8. **Nested segments double-counted.** Intel's CCG and DCAI are tagged on the same axis as their
   parent. Parent relationships now come from the filing's definition linkbase (`parent_segment`).
9. **Stale members listed.** Members with no value for the period (recast/discontinued) are
   left out and named in `note`.
10. **Reconciling items invisible.** Eliminations/corporate amounts are summarised in `note` so
    segment totals can be reconciled.

Schema: new `status_note` column on `financial_fact` and `segment_financial_fact`
(migration `b8c9d0e1f2a3`, which also clears stored `financial_fact` rows so old values are re-derived).

## 5. Missing and conflicting data behaviour (step 5)

Confirmed by tests and by the runs above:

- No tag for a concept → `MISSING`, `value = null`, no substitute (JPM/GE/BRK operating income,
  BRK shares and debt).
- Same period, different values across filings → `CONFLICTING` with the values and filings in `status_note` (GE FY2023).
- Derived values are `DERIVED` and name their components; if a component is itself restated the
  derived result is `CONFLICTING`.
- Segment facts with no pure-segment value → `MISSING`; disagreeing disclosures → `CONFLICTING`.

## 6. Remaining known gaps (not fixed)

- **JPM total debt** is long-term debt only (435,206M); short-term borrowings (64,776M in companyfacts) are not added, so the "total_debt" label overstates what it covers.
- **JPM cash** is the restricted-cash-inclusive cash-flow figure; no balance-sheet cash line was verified.
- **BRK-B**: no consolidated debt or share count (MISSING by design); cash differs from the balance-sheet lines by restricted cash; segment members McLane and Pilot Travel Centers appear alongside Service & Retailing but the filing's definition linkbase is only in the XBRL zip, so `parent_segment` is not set and summing all segments would double-count.
- **GE** segment operating income: the matched filing row was labelled "Gross profit (loss)", unconfirmed as segment profit.
- **JPM** segment amounts are on the firm's managed basis, so they do not add to consolidated revenue (the reconciling items are in `note`). Segment operating income was not extracted.
- **GOOGL shares** are the balance-sheet class total, not the cover page.
- **Unverified**: BRK-B Insurance/BNSF/BHE/Service & Retailing/Pilot segment amounts were not located on a saved filing page; AAPL cover-page share count was verified through companyfacts only; JPM/BRK cash through companyfacts only.
- Segment display names are derived from the member name (for example "Assetand Wealth Management Segment") and are cosmetic.
- Only the latest 10-K was validated for these six companies; earlier years and 10-Q periods were not.
- The full test suite was run on SQLite locally (847 passed, 3 skipped before the new regression file). The PostgreSQL run was not repeated locally; CI covers it.
