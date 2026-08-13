# Valuation Methodology

## Status

Implemented as of Phase 5. `backend/valuation/` contains the deterministic
engine described below; `backend/data/business_classifier.py` implements the
business classification described in "Business classification". All
functions are pure (no I/O, no network, no DB access) and fully unit-tested
against hand-calculated expected values (see `backend/tests/test_valuation_*.py`,
`backend/tests/test_business_classifier.py`). `POST /valuation/sotp` exposes
the SOTP engine directly; full ticker-driven runs with `ValuationRun`
persistence are deferred (see `docs/implementation-plan.md`, Phase 5).

## Principle

All valuation math is **deterministic**: given the same `FinancialFact`
inputs and the same approved `AssumptionDecision` values, the engine always
produces identical output. No AI model participates in the arithmetic. AI
may only *suggest* assumption inputs (growth rates, discount rates,
multiples), which a human must approve before they enter a `ValuationRun`.

## Business classification

`classify_business(sic_code)` maps a company's SEC SIC code (4-digit, from
EDGAR submissions JSON, public/standardized) to a business category via an
explicit, ordered SIC-range table, then looks up that category's fixed,
auditable set of eligible valuation methodologies. High-level ranges:

| SIC range(s) | Category | Eligible methodologies |
|---|---|---|
| 6020-6036, 6060-6062, 6090-6099, 6120-6199, 6712 | Bank | P/B, P/E, DDM |
| 6300-6399 | Insurance | P/B, P/E, DDM |
| 6798 | REIT | NAV, P/FFO, AFFO |
| 1000-1099, 1200-1299, 1400-1499 | Mining | EV/EBITDA, NAV |
| 1300-1399, 2900-2999, 4900-4999 | Energy | EV/EBITDA, NAV |
| 2830-2836, 3826-3829, 3841-3845, 8000-8099 | Healthcare | DCF, EV/EBITDA, Comps |
| 7370-7379 | Software | DCF, EV/Revenue, EV/EBITDA |
| 3570-3579, 3660-3679 | Technology | DCF, EV/Revenue, EV/EBITDA |
| 4800-4899 | Communication Services | DCF, EV/EBITDA, Comps |
| 2000-2199, 2300-2399 | Consumer Staples | DCF, EV/EBITDA, Comps, P/E |
| 5200-5999 | Consumer | DCF, EV/EBITDA, Comps, P/E |
| 2800-2899, 3200-3599, 3700-3799, 4000-4799 | Industrial | DCF, EV/EBITDA, Comps |
| 8731-8734, 6770 | Growth/Early Stage | DCF, EV/Revenue |
| n/a (assigned upstream, e.g. by segment/SOTP context) | Conglomerate | SOTP, DCF, Comps |
| unmatched / missing SIC | Other | DCF, Comps |

A category never appears without a matching methodology entry, and an
unmatched or missing SIC code classifies as "Other" rather than guessing.
This ruleset is what a later AI recommendation layer (Phase 6) is checked
against — never the other way around.

## Methods (implemented)

### 1. WACC (`backend/valuation/wacc.py`)

CAPM cost of equity:

```
Ke = risk_free_rate + beta * equity_risk_premium
```

After-tax cost of debt:

```
Kd_after_tax = cost_of_debt_pretax * (1 - tax_rate)
```

Weighted average cost of capital:

```
WACC = Ke * E/(D+E) + Kd_after_tax * D/(D+E)
```

where `E` and `D` are the market values of equity and debt supplied by the
caller. All inputs (risk-free rate, beta, ERP, cost of debt, tax rate,
capital weights) are required — nothing is hardcoded.

### 2. DCF (`backend/valuation/dcf.py`)

For each forecast year `i` (from explicit, caller-supplied per-year
schedules — never invented):

```
revenue_i = revenue_(i-1) * (1 + growth_rate_i)
ebit_i    = revenue_i * ebit_margin_i
nopat_i   = ebit_i * (1 - tax_rate)
da_i      = revenue_i * da_pct_i
capex_i   = revenue_i * capex_pct_i
nwc_change_i = revenue_i * nwc_change_pct_i
fcff_i    = nopat_i + da_i - capex_i - nwc_change_i
```

Terminal value (Gordon Growth / perpetuity growth), applied after the
explicit forecast horizon:

```
TV = FCFF_(n+1) / (WACC - g),   FCFF_(n+1) = FCFF_n * (1 + g)
```

(requires `WACC > g`, or the engine raises rather than returning an infinite
or negative terminal value). Each year's FCFF and the terminal value are
discounted to present value at `WACC`, summed to enterprise value:

```
EV = sum(FCFF_i / (1+WACC)^i) + TV / (1+WACC)^n
```

EV -> equity value bridge:

```
equity_value = EV - net_debt + cash_and_equivalents + investments - minority_interest
implied_price_per_share = equity_value / diluted_shares_outstanding
```

The result includes every year's full projection row (revenue, EBIT, NOPAT,
D&A, CapEx, NWC change, FCFF, discount factor, discounted FCFF) for audit
trail.

### 3. Comparable company analysis ("comps") (`backend/valuation/comps.py`)

For an already-assembled peer set (peer selection happens upstream — this
module does not choose peers):

```
EV_peer = market_cap + debt - cash
EV/Revenue, EV/EBITDA, EV/EBIT = EV_peer / (revenue | ebitda | ebit)
P/E  = market_cap / net_income
P/B  = market_cap / book_value_equity
```

Summary statistics (min, 25th percentile, median, 75th percentile, max) are
computed across the peer set via `numpy.percentile`. Applying a chosen
multiple (also chosen upstream, e.g. the peer median EV/EBITDA) to the
target's own metric gives the implied valuation:

```
implied_EV     = target_metric * chosen_multiple      (EV-based multiples)
implied_equity = implied_EV - target_debt + target_cash
implied_equity = target_metric * chosen_multiple       (equity-based multiples: P/E, P/B)
implied_price  = implied_equity / target_diluted_shares_outstanding
```

### 4. Sum-of-the-Parts (SOTP) (`backend/valuation/sotp.py`)

Each segment's enterprise value is computed upstream (via DCF and/or comps
on `SegmentFinancialFact` data) and passed in along with an explicit
ownership percentage:

```
attributed_EV_segment = segment_EV * ownership_pct
sum_of_segment_EVs    = sum(attributed_EV_segment for all segments)
non_operating_assets  = cash + marketable_securities + other_investments
```

Two corporate-cost treatments are always computed and returned side by
side, so both are visible regardless of which is "selected":

```
equity_value_direct_deduction = sum_of_segment_EVs + non_operating_assets
                               - total_debt - minority_interest - corporate_liabilities

capitalized_overhead_deduction = corporate_overhead_annual * overhead_capitalization_multiple
equity_value_capitalized_overhead = equity_value_direct_deduction - capitalized_overhead_deduction
```

`equity_value` is whichever of the two the caller selects via
`corporate_overhead_treatment`. Implied price per share divides by diluted
shares outstanding. If a current market share price is supplied, the
conglomerate discount/premium (equivalently, upside/downside) is:

```
pct = (implied_price_per_share - current_share_price) / current_share_price * 100
```

(negative = trading at a discount to the SOTP-implied value).

### 5. Sensitivity analysis (`backend/valuation/sensitivity.py`)

Builds a 2D matrix by re-running an existing valuation function (DCF or
comps) once per combination of two swapped-out inputs across explicit grids
(e.g. WACC rows × terminal growth rate columns for DCF, or EBITDA rows ×
chosen-multiple columns for comps), reading off one scalar output field
(default `implied_price_per_share`) per cell. No new math — purely
mechanical re-application of the DCF/comps formulas above across a grid.

## Inputs and provenance

Every valuation input is either:
- a `FinancialFact`/`SegmentFinancialFact` with `data_status = REPORTED` or
  `DERIVED`, or
- an `AssumptionDecision` with a human-approved value.

No hardcoded or fabricated numbers are permitted anywhere in the engine or in
UI placeholders — until a value is supplied by real data or an approved
assumption, the UI must show "—" rather than sample figures.

## Versioning

Each computation produces a `ValuationRun` row capturing the full input set,
method, and output, so any historical valuation can be reproduced exactly.
(Wiring real ticker-driven runs into `ValuationRun` persistence is deferred
beyond Phase 5's engine scope — see `docs/implementation-plan.md`.)

## Scenario engine (`backend/valuation/scenarios.py`)

BULL / BASE / BEAR is three explicit, fully-formed `DcfInput` sets supplied
by the caller (an analyst, possibly seeded by a governance-approved AI
assumption recommendation) — there is no built-in "bull = +X%" default.
`run_scenarios()` runs each through Phase 5's unmodified `run_dcf` and
returns all three outcomes side by side:

    upside_downside_pct = (implied_price_per_share - current_market_price)
                           / current_market_price * 100

`ordering_valid` is a diagnostic flag (`bull.EV >= base.EV >= bear.EV`)
surfaced to the caller/UI; it is never enforced by raising.

## Reverse valuation (`backend/valuation/reverse_valuation.py`)

Given a DCF input with everything fixed except one assumption, and a target
enterprise value (typically `market_cap + net_debt`), `solve_reverse_valuation`
uses `scipy.optimize.brentq` to find the value of that one assumption that
reproduces the target EV. Two solve targets are supported:

- `revenue_growth` — solves for a single flat YoY growth rate applied
  uniformly across every forecast year (replaces `revenue_growth_rates`).
- `terminal_growth` — solves for `terminal_growth_rate`.

If the target EV is not bracketed within the caller-supplied
`[search_low, search_high]`, the solve raises `ValueError` rather than
returning an extrapolated or fabricated answer.

**Classification thresholds** (deterministic, quantile-based, not AI):

    combined_low  = min(historical_range.low, peer_range.low)
    combined_high = max(historical_range.high, peer_range.high)
    position = (implied_value - combined_low) / (combined_high - combined_low)

| position range        | classification |
|------------------------|----------------|
| `position < -0.5`      | EXTREME        |
| `-0.5 <= position < 0` | CONSERVATIVE   |
| `0 <= position <= 1`   | REASONABLE     |
| `1 < position <= 1.5`  | AGGRESSIVE     |
| `position > 1.5`       | EXTREME        |

Both `historical_range` and `peer_range` are required inputs — never
invented defaults. An optional AI task
(`backend/ai/tasks/reverse_valuation_explainer.py`) may explain an
already-finalized result in prose; it never recomputes the number, and any
explanation containing a numeric token that can't be traced back to the
structured result (via `backend.ai.validation.validate_ai_numbers`) is
discarded.

## Value-unlock engine (`backend/valuation/value_unlock.py`)

Given a segment's current attributed EV (or current equity value / share
price, depending on action type) and explicit, caller-supplied
multiples/proceeds/amounts, computes current value vs. potential value vs.
estimated uplift for six structural actions:

| action | formula |
|---|---|
| `spin_off` / `segment_separation` | `potential = standalone_metric * standalone_multiple`; `uplift = potential - current_segment_value` |
| `subsidiary_ipo` | same as spin-off, times `(1 - ipo_discount_pct)` |
| `asset_sale` | `potential = sale_proceeds` (caller-supplied, not priced here) |
| `buyback` | `new_price = (current_equity_value - buyback_amount) / (shares_outstanding - buyback_amount / current_share_price)`; `uplift = new_price - current_share_price` |
| `debt_reduction` | `potential = current_equity_value + new_leverage_multiple_delta` (mechanically neutral unless caller supplies an explicit re-rating delta) |
| `special_dividend` | net holder uplift = `(equity_after_payout + re_rating_multiple_delta + dividend_amount) - current_equity_value` (zero by construction unless a re-rating delta is supplied) |

No multiple, discount, or proceeds figure is ever defaulted — every one is a
required function parameter. AI never touches this module directly; an
upstream task (`backend/ai/tasks/value_unlock_ideas.py`) may only propose
*which* action types are worth modeling and why, reusing Phase 7's
`approval.propose()`/`AssumptionDecision` pattern so a human must
APPROVE/EDIT/REJECT each idea before `backend/api/routers/scenarios.py`'s
`/value-unlock/compute/{action_type}` endpoint will run the numbers on it.
The AI proposal's result type (`ValueUnlockIdea`) has no field capable of
holding a dollar figure at all — `ACTION_TYPE_CODES` is a fixed categorical
id-per-action-type used only so a proposal can be stored on
`AssumptionDecision.ai_recommended_value` (a float column) without smuggling
a dollar amount into it.

## Risk dashboard (`backend/valuation/risk_dashboard.py`)

Six categories, four rule-based and two AI-assisted:

| category | basis | thresholds |
|---|---|---|
| Data Risk | segment/financial-fact coverage % | `>=85` LOW, `60-85` MEDIUM, `<60` HIGH |
| Model Risk | methodologies used + sensitivity spread % | `0 methodologies` → HIGH; `>=2 & spread<=20%` → LOW; `spread>40%` → HIGH; else MEDIUM |
| Forecast Risk | reverse-valuation classification | CONSERVATIVE/REASONABLE → LOW; AGGRESSIVE → MEDIUM; EXTREME → HIGH |
| Market Risk | beta + 1y volatility % | `beta<=1.1 & vol<=30` → LOW; `beta<=1.5 & vol<=50` → MEDIUM; else HIGH; both missing → HIGH (absence of data is not assumed benign) |
| Strategic Risk | AI-assisted | LOW/MEDIUM/HIGH only |
| Execution Risk | AI-assisted | LOW/MEDIUM/HIGH only |

Strategic Risk and Execution Risk are scored by
`backend/ai/tasks/risk_explanation.py`: the AI must answer with exactly one
of `LOW`/`MEDIUM`/`HIGH` for each category; any other value (free text, an
invented level, anything not in the closed enum) causes the whole task to
abstain rather than coerce the response into a guess. `RiskCategoryScore`
lives in `backend/schemas/valuation.py` (not `backend/valuation/`) so the AI
task can share the type without importing `backend.valuation`, preserving
the Phase 6 rule that `backend/ai/` never imports `backend/valuation/`.
