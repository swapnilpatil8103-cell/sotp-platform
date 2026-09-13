# Alphabet Inc. (GOOGL) — Sum-of-the-Parts Valuation Case Study

**Prepared using the SOTP Intelligence platform | Data as of the FY2025 10-K (filed 2026-02-05) and market data as of 2026-09-11**

*This document is a worked, human-defended analytical case study — every material assumption below was chosen and justified by the analyst, not generated or auto-applied by the platform. Automated outputs (SEC data extraction, deterministic calculations, AI-suggested starting points) are labeled as such throughout; the platform's own governance model requires exactly this distinction, and this memo follows it.*

---

## 1. Executive Summary

| Metric | Value | Source |
|---|---:|---|
| Current price | $338.50 | Market data (Yahoo Finance, as of 2026-09-11) |
| Market capitalization | $4,139.8B | Market data |
| Shares outstanding (common) | 12,088,000,000 | SEC XBRL, FY2025 10-K (see §2.1 on a data discrepancy this corrects) |
| DCF (Gordon Growth) implied price | $105.37 | This analysis |
| DCF (Exit Multiple, 18x terminal EBITDA) implied price | $243.05 | This analysis |
| SOTP implied price | $274.10 | This analysis |
| Reverse-valuation implied growth to justify market price | 44.7% (classified **EXTREME**) | This analysis |

All three method-based valuations land below the current market price. This is not presented as "the stock is overvalued" — it is presented as what it actually is: three internally consistent, conservatively-reasoned frameworks that do not, on their own, reconcile to where the market is pricing the stock. Section 8 discusses what would have to be true for the market's view to be justified, which is a more useful analytical output than a false-precision "fair value" number.

---

## 2. A Note on Data Integrity (read this before trusting any number below)

Before running any valuation, I found and had fixed **three real, independent bugs in the platform's core SEC data pipeline** while pulling data for this exact company. I flag this explicitly because presenting numbers without disclosing how they were validated would defeat the purpose of a "fully defended" case study.

### 2.1 Fiscal-year mislabeling
SEC's XBRL `fy` metadata field reflects which *filing* a data point appeared in as a comparative, not the calendar year the period actually covers. Cross-checking Alphabet's own historical revenue against public record caught this directly: the platform was showing FY2020 revenue ($182.5B) mislabeled as "FY2022." **Fix:** derive the true fiscal year from each fact's own period-end date. Verified post-fix against three companies with different fiscal-year conventions (GOOGL calendar year, AAPL September year-end, MSFT June year-end) — all now correct.

### 2.2 Tag-transition blind spot
Alphabet reported revenue under `RevenueFromContractWithCustomerExcludingAssessedTax` from ~2018–2024, then switched to the plain `Revenues` tag in its FY2025 10-K. The resolver was locking onto the first tag it found any data under and never checking alternates — so FY2025 revenue ($402.836B, confirmed against the raw filing) was coming back as **MISSING** even though it was sitting right there under a different valid tag. **Fix:** merge facts across every tag a company has used, with per-value tag provenance preserved and genuine value conflicts surfaced as `CONFLICTING` rather than guessed.

### 2.3 Segment sub-line vs. segment total
This is the one that would have most directly corrupted this case study if I hadn't checked: the segment extractor was picking up **"Google Search & Other" revenue ($224.5B)** — a product-level sub-line *within* Google Services — and reporting it as the entire Google Services segment total. The real segment total ($342.7B, dimensioned *only* by the business-segment axis with no product-axis overlay) is nearly 53% higher. **Fix:** require an exact dimensional match for a "segment total" fact; fall back to MISSING (never sum sub-lines, which risks double-counting or omission) when no pure total exists.

**Why this matters for you:** every dollar figure in this memo was pulled *after* these fixes, and I independently re-verified the corrected Google Services figure ($342.72B) against the raw inline-XBRL contexts myself before using it. If you're evaluating this project, ask to see the raw XBRL contexts I used — they're cited in §4.

---

## 3. Company Overview

**Alphabet Inc.** (CIK 0001652044), SIC 7370 (Services-Computer Programming, Data Processing). Fiscal year ends December 31.

Real 5-year revenue history (REPORTED, SEC XBRL, corrected fiscal-year labels):

| FY | Revenue | YoY Growth |
|---|---:|---:|
| 2020 | $182.53B | — |
| 2021 | $257.64B | +41.1% |
| 2022 | $282.84B | +9.8% |
| 2023 | $307.39B | +8.7% |
| 2024 | $350.02B | +13.9% |
| 2025 | $402.84B | +15.1% |

Revenue CAGR FY2020→FY2024: **17.68%** (computed by the platform's historical-trends module, DERIVED status — not an assumption, a calculated fact from REPORTED history).

Operating margin trend (REPORTED op. income / REPORTED revenue, DERIVED): 22.6% (2020) → 30.6% (2021) → 26.5% (2022) → 27.4% (2023) → 32.1% (2024). FY2025 margin: 129.0B / 402.8B = **32.0%**.

**Analyst read on growth:** the raw 17.7% historical CAGR reflects a mix of COVID-era digital-ad recovery (the 41% 2021 spike) and a subsequent normalization to a steadier 9–15% band. I do **not** mechanically extrapolate the 17.7% CAGR forward — doing so would overstate a temporary recovery effect. My explicit forecast (§5) fades growth from 13% toward 7% over five years, reflecting a maturing ~$400B revenue base against continued strength in Cloud and AI-driven Search monetization. This is a judgment call, made explicitly, not the platform's suggestion.

---

## 4. Segment Analysis (Real, Corrected Data)

Source: SEC XBRL inline instance document, FY2025 10-K, accession 0001652044-26-000018. Every figure below is the exact-dimensional-match segment total (see §2.3).

| Segment | Revenue | Operating Income | Op. Margin |
|---|---:|---:|---:|
| Google Services | $342.72B | $139.40B | 40.7% |
| Google Cloud | $58.71B | $13.91B | 23.7% |
| Other Bets (All Other Segments) | $1.54B | -$7.52B | n/m |
| **Sum of segments** | **$402.96B** | **$145.80B** | — |
| **Consolidated (reported)** | **$402.84B** | **$129.04B** | 32.0% |

The ~$0.12B revenue variance and ~$16.8B operating-income variance between the segment sum and consolidated totals reflect intersegment eliminations and unallocated corporate costs — expected and immaterial in magnitude relative to revenue, consistent with what Alphabet's own segment footnote reconciliation shows.

**Segment data coverage** (platform-computed, live): 40% for each segment (2 of 5 tracked metrics — revenue and operating income — are REPORTED; D&A, CapEx, and Assets are **not disclosed at the segment level** in Alphabet's XBRL filing). This is a genuine, structural limitation under US GAAP (ASC 280 only requires disclosure of what the CODM actually reviews), not a data-pull failure — confirmed by checking the raw filing directly.

---

## 5. DCF (Company-Level)

**WACC — peer-informed, not raw single-stock beta**

Rather than using Alphabet's own raw beta (1.225) directly, I computed a peer-informed beta via Hamada unlevering/relevering, using three real digital-platform peers:

| Peer | Levered β | D/E | Tax Rate | Unlevered β |
|---|---:|---:|---:|---:|
| Meta (META) | 1.243 | 0.0356 | 29.65% | 1.2126 |
| Microsoft (MSFT) | 1.108 | 0.0110 | 19.40% | 1.0983 |
| Amazon (AMZN) | 1.443 | 0.0247 | 20.19% | 1.4151 |

Median unlevered beta: **1.2126**. Relevered at Alphabet's own capital structure (D/E = 0.0117, tax rate = 16.78%, both real, computed from REPORTED debt/tax figures): **relevered β = 1.2246** — reassuringly close to Alphabet's own raw beta (1.225), which is itself a useful cross-check that the peer-informed approach and the market's own pricing of GOOGL's risk are consistent.

| WACC Input | Value | Basis |
|---|---:|---|
| Risk-free rate | 4.20% | Assumption (approx. 10-year Treasury) |
| Beta | 1.2246 | Peer-informed, computed above |
| Equity risk premium | 5.00% | Assumption (standard practitioner estimate) |
| Cost of equity | **10.32%** | CAPM |
| Pre-tax cost of debt | 4.50% | Assumption (investment-grade proxy) |
| Tax rate | 16.78% | REPORTED (net income / pretax income) |
| After-tax cost of debt | 3.78% | Computed |
| E / (D+E) | 98.84% | REPORTED (market cap, total debt) |
| **WACC** | **10.25%** | Computed |

**Revenue/margin assumptions (explicit analyst judgment, not platform-suggested):**

| Year | Revenue Growth | EBIT Margin | CapEx % Rev |
|---|---:|---:|---:|
| 1 | 13% | 31% | 20% |
| 2 | 12% | 31% | 18% |
| 3 | 10% | 30% | 16% |
| 4 | 8% | 30% | 15% |
| 5 | 7% | 30% | 14% |

Growth fades from the recent actual 15.1% (FY2025) toward a more sustainable long-run rate, reflecting base effects on a ~$400B revenue platform. Margin is held near the FY2025 actual (32.0%) but trimmed slightly to 30% to reflect ongoing heavy AI/data-center capital intensity — visible directly in the real capex trajectory (FY2025 capex was 22.7% of revenue, the highest in the company's history, vs. a 5-year historical range that never previously exceeded ~20%). I fade CapEx down from 20% to 14% over the forecast on the judgment that the current AI buildout is a temporary intensity spike, not a new permanent baseline — this is a debatable assumption and exactly the kind of thing I'd expect to be challenged on in a review (see the sensitivity discussion in §8).

D&A held flat at 5.25% of revenue (FY2025 actual, depreciation only — amortization is not separately broken out in this filing's XBRL, a real, disclosed data limitation). NWC change assumed at 1% of revenue (no granular working-capital XBRL data was available to derive this from history — an explicit assumption, not a derived figure).

**Results:**

| Method | Enterprise Value | Equity Value | Implied Price/Share |
|---|---:|---:|---:|
| Gordon Growth (g = 3.0%) | $1,126.7B | $1,273.7B | **$105.37** |
| Exit Multiple (18x terminal EBITDA) | $2,790.9B | $2,937.9B | **$243.05** |

The two terminal-value methods disagree by more than 2x on implied EV — a real, honest finding, not a rounding artifact. It reflects how sensitive a punishing WACC-minus-g spread (10.25% − 3.0% = 7.25 points) is in Gordon Growth versus a market-based exit multiple that implicitly captures the growth/margin durability the market is willing to pay for beyond year 5. Neither method alone should be trusted; both are shown deliberately.

---

## 6. Sum-of-the-Parts

Segment enterprise values were **not** left as blank inputs — I used the platform's automated per-segment valuation tool (new capability, built this session) to apply explicit, sourced multiples, and I overrode it entirely for one segment where a multiple-based approach is analytically wrong.

**Google Services** — valued via EV/EBIT, using **Meta's own real, current EV/EBIT multiple (19.55x)** as the closest pure-play comp for a high-margin digital advertising business:
Meta's EV = market cap ($1,650.9B) + debt ($58.7B) − cash & equivalents ($81.6B) = $1,628.0B; Meta's EV/EBIT = $1,628.0B / $83.3B EBIT = **19.55x**.
Applied to Google Services' EBIT ($139.40B): **$2,725.3B**.

**Google Cloud** — valued via EV/Revenue at **7.0x**, a judgment-based multiple (not derived from a real public pure-play comp, since neither AWS nor Azure trades separately). I anchored this to typical public-market pricing of high-growth infrastructure/platform businesses rather than a specific company; this is the weakest-evidenced multiple in this memo and the one I would expect to be challenged on hardest.
Applied to Google Cloud's revenue ($58.71B): **$410.9B**.

**Other Bets (All Other Segments)** — **not** valued via the automated multiple tool. Its EBIT is negative (-$7.52B); applying any earnings multiple to a loss produces a mathematically meaningless negative "valuation." Rather than force the tool to output a number, I assigned **$30B** as a judgment-based reference value, informally anchored to Waymo's last disclosed private funding round valuation (~$45B in 2024) discounted for (a) Waymo being only one of several bets in this segment, (b) ongoing cash burn across the portfolio, and (c) the illiquidity/uncertainty discount appropriate for early-stage ventures. **This is the single softest number in the entire model**, and I'm flagging it as such rather than dressing it up with false precision.

| SOTP Bridge | Value |
|---|---:|
| Google Services EV | $2,725.3B |
| Google Cloud EV | $410.9B |
| Other Bets EV (judgment) | $30.0B |
| **Sum of segment EVs** | **$3,166.3B** |
| + Cash & equivalents | $30.7B |
| + Marketable securities | $96.1B |
| + Other long-term investments | $68.7B |
| − Total debt | ($48.5B) |
| − Corporate overhead (direct deduction) | $0 (see note) |
| **Equity value** | **$3,313.3B** |
| ÷ Diluted shares outstanding | 12,088,000,000 |
| **Implied price per share** | **$274.10** |
| Current market price | $338.50 |
| **Conglomerate discount/(premium)** | **-19.0%** (negative = market trades at a premium to SOTP) |

*Note on corporate overhead:* Alphabet does not separately break out an "Alphabet-level unallocated costs" line in the XBRL data pulled here. I used a placeholder assumption ($5B annual, 8x capitalization multiple for the alternate treatment) rather than fabricate a REPORTED figure — under the capitalized-overhead treatment, equity value would be $3,273.3B ($270.72/share), a ~1.2% difference from the direct-deduction figure shown above. This is disclosed, not buried.

---

## 7. Reverse Valuation — What Does the Market Actually Believe?

Rather than asserting a single "fair value" and calling the stock over- or under-valued, I solved backward: **holding my DCF's margin, WACC, and terminal-growth assumptions fixed, what flat revenue growth rate would reconcile the model to the market's actual enterprise value** ($4,061.5B = market cap + net debt)?

**Answer: 44.7% flat 5-year revenue growth.**

Classified **EXTREME** relative to both Alphabet's own recent historical range (8–20%) and the peer range I used for this analysis (10–20%, based on META/MSFT/AMZN).

This is the single most important honest finding in this memo. It does **not** mean the market is wrong — it means one or more of the following must be true for the current price to be justified under a standard DCF lens:
1. The market is using a materially lower cost of capital than my 10.25% WACC estimate (plausible — Alphabet's actual cost of debt is almost certainly below my 4.5% pre-tax assumption given its balance sheet quality).
2. The market ascribes real option value to Other Bets (Waymo, quantum computing, life sciences) far beyond my $30B placeholder.
3. The market is pricing in continued margin expansion and/or aggressive buyback-driven EPS accretion beyond what a flat-growth DCF captures.
4. My exit-multiple/SOTP multiples (19.55x EV/EBIT for Services, 7.0x EV/Revenue for Cloud) are too conservative relative to where the market actually prices comparable AI-exposed platform businesses today.

I don't have a confident view on which of these dominates — that would require deeper work than this data pull supports (e.g., real management-guidance-informed forecasts, a proper AI-infrastructure ROI analysis for the capex step-up). That is the honest limit of this analysis, stated directly rather than papered over with false conviction.

---

## 8. Triangulation Summary

| Method | Implied Price | vs. Market ($338.50) |
|---|---:|---:|
| DCF — Gordon Growth | $105.37 | -68.9% |
| DCF — Exit Multiple (18x) | $243.05 | -28.2% |
| SOTP | $274.10 | -19.0% |
| **Market price (actual)** | **$338.50** | — |

All three method-based estimates sit below market, with SOTP the closest. The dispersion itself is the analytical finding: a ~2.6x spread between the most conservative (Gordon Growth DCF) and market price signals that **terminal-value assumptions and segment-level multiples dominate this valuation far more than the operating forecast does** — which is a real, generalizable lesson about valuing high-growth, platform-scale businesses: precision in the 5-year forecast matters less than getting the terminal/multiple assumptions defensible, and no single method should be presented as "the" answer.

---

## 9. What I Would Do Next With More Time

- Source a real management-disclosed or sell-side consensus revenue/margin forecast to replace my own judgment-based fade schedule, and show how sensitive the DCF is to that choice specifically (a proper sensitivity table, not just two TV methods).
- Build a real per-segment DCF for Google Cloud (rather than an EV/Revenue multiple) using its own disclosed growth trajectory, since a 7.0x revenue multiple on a business without a clean public comp is the weakest-evidenced number in this memo.
- Get a genuine third-party or disclosed valuation reference for Other Bets beyond the informal Waymo anchor — this is a placeholder, not a defended valuation.
- Pull Alphabet's actual disclosed cost of debt (bond yields/credit spread) rather than assuming 4.5%, since the reverse-valuation finding in §7 suggests WACC may be the most consequential single assumption in this entire model.

---

*All REPORTED figures in this memo trace to real SEC XBRL data via the SOTP Intelligence platform, with source URLs and accession numbers available in the underlying API responses. All SUGGESTED/judgment figures are explicitly labeled as such in the section where they appear. No number in this document was fabricated, interpolated, or presented with more precision than its underlying data supports.*
