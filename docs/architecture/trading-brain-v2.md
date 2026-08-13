# Trading Brain V2 Architecture

Trading Brain V2 is the target architecture for a single deterministic paper-trading decision brain. It is introduced as a shadow-mode system beside the existing active brain. This document describes the target shape only; it does not authorize a change to live execution behavior.

## Operating Principles

- Live trading remains disabled.
- Existing brain remains the active execution path until a separate migration ticket promotes V2.
- Trading Brain V2 starts in shadow mode only.
- No manual review state is allowed.
- Any review-like input must resolve to one deterministic machine action:
  - `ENTER_REDUCED`
  - `REFRESH_AND_RECHECK`
  - `BLOCK`
- Refresh-required candidates must never go directly to execution.
- Every candidate and position decision must be explainable, auditable, and reproducible from saved inputs.
- V2 must emit intent records first. Execution handoff remains a separate paper-only adapter step.

## L1-M01 Autopilot

### AP-B01 Gold Dataset Intake

Reads the latest approved Gold/Gold V2 model and feature artifacts that are eligible for trading. The block must validate artifact freshness, schema version, strategy version, and feature availability before any candidate is considered.

Inputs:
- Gold/Gold V2 rows.
- Model signal table.
- Candidate generation metadata.
- Pipeline manifest and strategy fingerprint.

Outputs:
- Versioned candidate source frame.
- Intake status.
- Missing-data warnings.

### AP-B02 Candidate Normalizer

Converts raw pipeline, model, AI2, and candidate fields into one canonical candidate schema. It preserves raw fields for audit while producing normalized fields for later blocks.

Canonical examples:
- `symbol`
- `source_rank`
- `source_trade_action`
- `proposed_side`
- `expected_return_bps`
- `expected_return_scope`
- `risk_tier`
- `volatility_tier`
- `order_ready`
- `ai2_decision`
- `ai2_warning_codes`

### AP-B03 Candidate Validity Gate

Rejects structurally invalid rows before any scoring. Invalid means the row cannot be safely evaluated, not that the trade is unattractive.

Hard failures:
- Missing symbol.
- Missing direction authority.
- Missing price.
- Missing notional or quantity context.
- Schema mismatch.
- Stale source artifact.

Output actions:
- `CONTINUE`
- `BLOCK`

### AP-B04 AI2 Status Interpreter

Maps AI2 enrichment decisions into deterministic machine state. AI2 may enrich and classify, but must not become an uncontrolled execution brain.

Allowed mappings:
- AI2 `Proceed candidate` -> `CONTINUE`
- AI2 `Review before execution` -> `ENTER_REDUCED` or `REFRESH_AND_RECHECK`, based on warning class.
- AI2 `Do not execute until refreshed` -> `REFRESH_AND_RECHECK`
- AI2 rejection or missing enrichment when required -> `BLOCK`

Review is not a final state.

### AP-B05 Warning Interpreter

Converts warning codes into deterministic actions. The interpreter must be table-driven and auditable.

Examples:
- High volatility with otherwise valid candidate -> `ENTER_REDUCED`
- Large intraday move -> `REFRESH_AND_RECHECK`
- Stale quote -> `REFRESH_AND_RECHECK`
- Missing intraday data when required -> `BLOCK`
- Liquidity warning above hard floor but below normal quality -> `ENTER_REDUCED`

### AP-B06 Refresh Gate

Decides whether the candidate requires a fresh quote, intraday bar, or AI2 enrichment rerun before execution.

Rules:
- `REFRESH_AND_RECHECK` must stop the current execution chain.
- A refreshed candidate must re-enter from AP-B02 or AP-B04, not skip forward.
- Refresh attempts and outcomes must be logged.

### AP-B07 Tradability Gate

Applies non-negotiable trading constraints.

Checks include:
- Price floor.
- Market-cap floor.
- Average dollar-volume floor.
- Session eligibility.
- Overnight tradability when applicable.
- Short-side disabled unless explicitly enabled in a future segment.
- Existing position and open-order conflicts.
- Anti-churn and re-entry cooldown.

Output actions:
- `CONTINUE`
- `BLOCK`

### AP-B08 Risk Scoring Engine

Computes risk-aware candidate quality without changing model scoring. This block combines validated expected return, volatility, spread, liquidity, portfolio exposure, and warning severity into an execution risk score.

It must distinguish:
- Gross model edge.
- Net-of-cost edge.
- Execution quality.
- Portfolio risk contribution.
- Confidence source and calibration status.

### AP-B09 Position Sizing Engine

Converts candidate quality into paper notional and quantity. It must respect existing caps, validation limits, account equity, session-size multipliers, and reduction rules.

Outputs:
- `target_notional`
- `target_quantity`
- `sizing_reason`
- `sizing_multiplier`
- `sizing_cap_applied`

### AP-B10 Entry Decision Engine

Makes the final entry decision from all previous block outputs.

Allowed decisions:
- `ENTER`
- `ENTER_REDUCED`
- `REFRESH_AND_RECHECK`
- `BLOCK`

No `manual_review` decision is allowed.

### AP-B11 Trade Intent Builder

Builds a normalized intent record, not a broker order. The intent must include all fields required for audit and later execution.

Required intent fields:
- `intent_id`
- `cycle_id`
- `strategy_version`
- `symbol`
- `side`
- `target_notional`
- `target_quantity`
- `decision`
- `decision_reason`
- `blocking_reason`
- `source_candidate_id`
- `ai2_enrichment_id`
- `session_mode`
- `risk_score`
- `audit_payload`

### AP-B12 Execution Handoff

Hands accepted paper intents to the existing execution adapter. This block must remain paper-only during the V2 shadow period.

Shadow mode behavior:
- Write what V2 would submit.
- Do not submit through V2.
- Compare V2 intents with existing brain actions.

Promoted mode behavior, future only:
- Submit paper orders through the approved existing paper execution adapter.
- Live paths remain disabled.

## L1-M02 Position Management

### PM-B01 Position Creation

Creates a V2 position record from a filled broker order. The position must link back to the candidate, intent, broker order, and fill.

### PM-B02 Initial Risk Attachment

Attaches stop, take-profit, max-hold, trailing, and portfolio-risk metadata at position creation time.

### PM-B03 Live Mark-to-Market

Refreshes current price, unrealized P/L, return, spread, liquidity status, and quote freshness.

### PM-B04 Stop-Loss Engine

Evaluates hard loss rules. Stop-loss actions must be deterministic and must cite the breached threshold.

Allowed decisions:
- `HOLD`
- `EXIT`
- `REDUCE`

### PM-B05 Profit-Taking Engine

Evaluates realized opportunity capture. It must distinguish between small profit noise, meaningful profit, and profit that should be protected.

Allowed decisions:
- `HOLD`
- `REDUCE`
- `EXIT`
- `TRAIL`

### PM-B06 Trailing Stop Engine

Tracks peak return and giveback. It may only trigger after the configured profit arm threshold is reached.

### PM-B07 Time-Based Exit Engine

Evaluates holding-period rules. Time alone should produce a deterministic rule-based decision, and re-entry logic must be evaluated before unnecessary churn.

### PM-B08 Portfolio Risk Overlay

Applies portfolio-level controls:
- Gross exposure.
- Net exposure.
- Sector concentration.
- Red-position percentage.
- Basket drawdown.
- Correlation concentration where available.

### PM-B09 Re-Entry / Add-On Logic

Decides whether an existing position should be held, added to, rolled, or blocked from re-entry. This block prevents exit-and-reopen churn when the same symbol remains attractive.

Allowed decisions:
- `HOLD`
- `ADD`
- `ROLL`
- `BLOCK_REENTRY`

### PM-B10 Exit Decision Engine

Combines PM-B04 through PM-B09 into a single final position action.

Allowed decisions:
- `HOLD`
- `REDUCE`
- `EXIT`
- `ADD`
- `ROLL`
- `BLOCK`

No manual review state is allowed.

### PM-B11 Performance Attribution

Attributes every position outcome to entry source, AI2 enrichment, warnings, gate state, sizing, session, exit reason, and realized P/L.

### PM-B12 Feedback Store

Writes outcome data used by later diagnostics and model validation. Feedback is read-only for the active segment unless a future ticket explicitly promotes it into model or gate behavior.

## Shadow Mode Contract

In shadow mode, V2 must:
- Read the same candidate, position, broker, and config state as the existing brain.
- Produce candidate decisions and position decisions.
- Write V2 audit artifacts.
- Never submit orders.
- Never close, reduce, or increase positions.
- Never change active candidate ranking.
- Never change active position management.

## Active Path During Introduction

Existing execution remains active:

```text
pipeline candidates
  -> execution_ranked_candidates_*.csv
  -> existing paper_autopilot.tick()
  -> existing apply_auto_open()
  -> existing Alpaca paper adapter
```

V2 shadow path runs beside it:

```text
same inputs
  -> Trading Brain V2 blocks
  -> shadow intents
  -> audit and diff reports only
```

