# AI Governance

## Status

Phase 6 implemented `backend/ai/` against this contract (see
`docs/implementation-plan.md` Phase 6). `backend/governance/` (the human
approval-gate wiring around the proposals produced here) is still Phase 7.

## What the AI layer is allowed to do (as implemented)

`backend/ai/tasks/` exposes exactly six functions, each producing a typed,
ABSTAIN-capable result:
- `research_summary.generate_research_summary` — summarize
- `methodology_recommendation.recommend_methodology` — classify/rank (never
  outside `backend/data/business_classifier.py`'s deterministic eligible list)
- `peer_recommendation.recommend_peers` — recommend (flagged
  `requires_human_approval=True`, never written to `comps.py` inputs)
- `assumption_recommendation.recommend_assumptions` — propose DCF/WACC
  assumption values (shaped to map onto `AssumptionDecision.ai_*` fields;
  `requires_human_approval=True` on every proposal)
- `devils_advocate.run_devils_advocate` — challenge a completed valuation
  (`advisory_only=True`; never mutates the valuation object it is given)
- `memo_generator.generate_memo_section` — draft memo prose from
  already-computed data

Nothing in `backend/ai/` computes a valuation, writes to `FinancialFact`,
`SegmentFinancialFact`, or `AssumptionDecision.approved_value`, or imports
`backend/valuation/` (enforced by an AST-based test,
`backend/tests/test_ai_import_boundary.py`, so this can't silently regress).

## What the AI layer is forbidden from doing (as implemented)

1. Perform authoritative financial calculations — no task touches
   `backend/valuation/`; it only receives already-computed dicts.
2. Fabricate financial facts — every task builds its prompt strictly from
   caller-supplied real data (SEC/market-derived `FinancialFact`/
   `SegmentFinancialFact`/valuation-output dicts, never invented in-prompt),
   and every task's text output is run through the guardrail below before
   being accepted.
3. Overwrite source data or historical values — no write path exists from
   `backend/ai/` to any model.
4. Silently estimate missing values — every task has an explicit ABSTAIN
   path (`abstained=True`, `reason="Insufficient evidence to make a
   reliable recommendation."` or a guardrail-failure reason) triggered when
   input data is empty, the adapter is unavailable, the response doesn't
   parse, or the guardrail rejects fabricated numbers.
5. Modify formulas/calculations — not reachable; see (1).
6. Override human decisions — `peer_recommendation` and
   `assumption_recommendation` results are explicitly marked as requiring
   human approval; nothing in this phase auto-applies them (Phase 7 wires
   the actual approval gate).

## The guardrail (`backend/ai/validation.py`)

`validate_ai_numbers(ai_text, input_data)` is the concrete, code-level
enforcement of "AI cannot fabricate financial facts":
1. Recursively flattens every int/float in `input_data` (dicts, lists,
   dataclass-like objects) into a set of allowed numbers.
2. Regex-extracts numeric tokens (including `%`) from `ai_text`.
3. Skips tokens under a small-magnitude threshold (default 4) and
   plausible bare calendar years (1990-2099), since these are structural,
   not financial facts, and would otherwise cause constant false positives.
4. For every remaining token, accepts it if it matches an allowed number
   within 0.5% relative tolerance, or via a simple derived transform:
   rounding to 0/1/2 decimals, or percentage-vs-fraction equivalence
   (0.085 vs "8.5%").
5. Any token that matches nothing is reported in `unmatched_numbers`; the
   calling task then discards the AI output and returns an ABSTAIN result
   rather than surfacing text containing an untraceable number.

**Honest limitations** (documented in the module docstring, restated here):
it is a heuristic guardrail, not a proof. It cannot catch a fabricated
*qualitative* claim that uses no numbers, cannot verify a real input number
was used in a *truthful context* (e.g. attributed to the wrong segment), and
deliberately tolerates small numeric drift (rounding/formatting) rather than
flagging every restatement — so it should be read as "reduces the risk of
gross number fabrication," not "guarantees factual correctness." A human
reviewer remains the actual control for anything the guardrail can't see.

## Degraded-mode / error handling

`backend/ai/errors.py` defines `AIUnavailableError` (missing API key, SDK
init failure, network/call failure) and `AIRateLimitError` (quota/rate-limit
rejection); `backend/ai/gemini_adapter.py`'s `GeminiAdapter` raises only
these from `generate_text`, never letting a raw SDK exception escape. Every
task function catches these and converts them into an ABSTAIN result — the
system degrades gracefully (no AI output, clearly reasoned) rather than
crashing or substituting fabricated content. `backend/ai/testing.py`'s
`FakeAIAdapter` implements the same interface deterministically for tests,
and can simulate either error to exercise these paths without a live key.

## Rules

1. **AI never writes authoritative data.** The AI layer (`backend/ai/`) may
   only produce *recommendations* — proposed assumption values, flagged
   anomalies, drafted narrative text. It has no write path to `FinancialFact`,
   `SegmentFinancialFact`, or approved `AssumptionDecision.approved_value`.
2. **Every recommendation carries a rationale.** AI output is never a bare
   number; it is accompanied by the reasoning and the data it was derived
   from, so a human reviewer can evaluate it.
3. **Human approval is mandatory before use in a ValuationRun.** An
   `AssumptionDecision` is not usable by the valuation engine until a human
   user has approved, overridden, or rejected it. Overrides record the
   human's own value and reason.
4. **Every decision is attributed and timestamped.** `AssumptionDecision` and
   `AuditLogEntry` always store the acting user and a timestamp — there is no
   anonymous or silent approval path.
5. **AI calls are logged.** Every AI invocation (prompt, model, response,
   confidence if available) is recorded to the audit trail for reproducibility
   and cost/quality review.
6. **No silent retries that change meaning.** If an AI call is retried or a
   prompt changes, that is a new recommendation, not a hidden update to the
   old one.
7. **Deterministic core stays deterministic.** AI never enters the arithmetic
   path of `backend/valuation/`; see `docs/valuation-methodology.md`.

## Adapter interface

`backend/ai/adapter.py` defines the `AIAdapter` interface (unchanged in
shape since Phase 1, extended with `generate_text`). `backend/ai/
gemini_adapter.py` (`GeminiAdapter`) is the real Phase 6 implementation;
`backend/ai/testing.py` (`FakeAIAdapter`) is the deterministic test double.
Note: item 5 above ("AI calls are logged" to the audit trail) and the
`AssumptionDecision` persistence/approval flow itself are Phase 7 work —
Phase 6 produces the typed proposal data those will consume.

## Phase 7: the approve/edit/reject workflow, as implemented

`backend/governance/approval.py` is the only place that turns a Phase 6 AI
proposal into a persisted, human-actionable record.

- **Propose.** `approval.propose(session, company_id, assumption_key,
  ai_recommended_value, ai_rationale, ai_confidence, subject)` creates a
  `AssumptionDecision` row with `status=PENDING` and no `valuation_run_id`
  (it isn't tied to any run yet), and writes an `AuditLogEntry`
  (`action=AI_PROPOSAL_GENERATED`, `actor="ai"`). Callers get the
  `ai_recommended_value`/`ai_rationale` from a Phase 6 task's output
  (e.g. `assumption_recommendation.recommend_assumptions`); this function
  does not call the AI itself.
- **Decide.** `approval.record_decision(session, decision_id, decision,
  user_id, human_value=None, reason=None)` accepts `"APPROVE"`, `"EDIT"`, or
  `"REJECT"`:
  - APPROVE sets `approved_value = ai_recommended_value`, `status=APPROVED`.
  - EDIT requires `human_value`; sets `approved_value = human_value`,
    `status=OVERRIDDEN`, `approval_reason=reason`.
  - REJECT sets `approved_value=None`, `status=REJECTED`, `approval_reason=reason`.
  In every case `ai_recommended_value`/`ai_rationale` are left untouched, so
  both the AI's original number and the human's final number are visible on
  the same row indefinitely. A row that is not `PENDING` cannot be decided
  again (`GovernanceError`) -- a correction requires a fresh `propose()`
  call, so there is never an ambiguous "which decision is authoritative"
  state. Writes `AuditLogEntry` (`action=DECISION_RECORDED`,
  `actor="human:<user_id>"`).
- **The enforcement mechanism (concretely).** No function in
  `backend/governance/` or `backend/valuation/` will accept an assumption
  value straight from an `AssumptionDecision.ai_recommended_value` field.
  The only path from a decision row to a usable number is
  `backend/governance/valuation_run.py`'s `assemble_approved_assumptions()`,
  which re-reads each `decision_id` from the DB (not trusting any
  caller-supplied "already checked" flag) and raises
  `UnapprovedAssumptionError` unless `status` is `APPROVED` or `OVERRIDDEN`
  and the row's `company_id` matches. `execute_valuation_run()` calls this
  same check again before writing anything, so a `ValuationRun` is
  structurally impossible to create from a still-`PENDING` or `REJECTED`
  assumption. This is tested directly (`test_running_with_pending_assumption_is_rejected`,
  `test_running_with_rejected_assumption_is_rejected` in
  `backend/tests/test_governance_valuation_run.py`).
- **`backend/governance/approval.py` never imports `backend.valuation`** —
  enforced by a structural test
  (`test_governance_module_never_imports_valuation_engine`), matching the
  same discipline Phase 6 used for the AI/valuation boundary.

## Versioning and diff (Phase 7)

`backend/governance/valuation_run.py`'s `execute_valuation_run()` computes
the next `ValuationRun.version` as `max(existing versions for this
company+method) + 1`, and stores a full snapshot in `inputs["_governance_snapshot"]`
(which `assumption_decision_ids` were consumed, the resolved
`assumptions_used` values, and an optional `source_data_version` blob for
peer-set/methodology/filing references) alongside the caller-supplied
`inputs`/`outputs` from the Phase 5 engine call. `diff_runs(run_a, run_b)` is
a pure, deterministic, template-based comparison (no LLM call): it flattens
both runs' `inputs`/`outputs` into dotted-key maps, reports every field whose
value differs, and separately highlights a curated subset of well-known
assumption/output fields (`wacc`, `terminal_growth_rate`, `peer_multiple`,
`sotp_value`, `per_share_value`, etc.) as `key_changes`.

## Audit trail retrieval (Phase 7)

`backend/audit/logger.py`'s `log_event()` is the single write path for
`AuditLogEntry`; `propose`/`record_decision`/`execute_valuation_run` all call
it automatically, so every material governance event is logged without each
caller having to remember to. `get_audit_trail(session, entity_type=,
entity_id=)` or `get_audit_trail(session, company_id=)` returns entries
oldest-first, each tagged with a `role` (`AI_INTERPRETATION` for AI
proposals, `INPUT` for human decisions, `CALCULATION_AND_OUTPUT` for
valuation runs, `SOURCE` for data ingestion events) so a caller can walk the
CLAIM->SOURCE->INPUT->CALCULATION->OUTPUT->AI INTERPRETATION chain for a
company or a single run. Ingestion-level audit hooks (logging
`DATA_INGESTED` from `backend/data/persistence.py`'s upsert helpers) are
**not** wired in this phase -- the upsert helpers are exercised by Phase 2/3's
existing passing test suite and wiring audit calls into them risked breaking
those tests for low marginal benefit; the audit write path is instead fully
demonstrated end-to-end via the governance/valuation-run flow above. This is
a noted follow-up, not a gap in the mechanism itself (`log_event` works for
any `entity_type`/`action`, so adding an ingestion call site later is
additive).
