# Trading Brain V2 Reference Blocks

This reference defines deterministic block inputs, outputs, allowed decisions, and audit expectations. It is intentionally implementation-neutral so the first implementation can run in shadow mode beside the existing brain.

## Shared Decision Vocabulary

Autopilot entry decisions:
- `ENTER`
- `ENTER_REDUCED`
- `REFRESH_AND_RECHECK`
- `BLOCK`

Position-management decisions:
- `HOLD`
- `REDUCE`
- `EXIT`
- `ADD`
- `ROLL`
- `BLOCK`

Forbidden final state:
- `MANUAL_REVIEW`
- `REVIEW`
- `UNKNOWN_ACTION`

Any review-like upstream state must map to a deterministic action.

## Shared Audit Fields

Every block should preserve or emit:

- `run_id`
- `cycle_id`
- `strategy_version`
- `brain_version`
- `shadow_mode`
- `input_artifact_path`
- `input_artifact_mtime`
- `symbol`
- `block_id`
- `block_decision`
- `block_reason`
- `supporting_reasons`
- `warnings`
- `data_quality_status`
- `created_at`

## L1-M01 Autopilot Blocks

### AP-B01 Gold Dataset Intake

Purpose: verify the candidate source has a valid upstream model/data foundation.

Inputs:
- Latest Gold/Gold V2 artifact.
- Latest model signal artifact.
- Pipeline manifest.
- Config fingerprint.

Outputs:
- `intake_status`
- `candidate_source_version`
- `pipeline_run_id`
- `strategy_version`
- `missing_input_reason`

Failure action:
- `BLOCK` for missing, stale, or incompatible source data.

### AP-B02 Candidate Normalizer

Purpose: convert all candidate-like rows into one canonical schema.

Inputs:
- Candidate pool rows.
- Execution-ranked rows.
- AI2 enrichment rows, when present.

Outputs:
- `normalized_symbol`
- `normalized_side`
- `source_trade_action`
- `candidate_rank`
- `execution_rank`
- `expected_return_bps`
- `expected_return_scope`
- `ai2_status`
- `warning_codes`

Failure action:
- `BLOCK` only for rows that cannot be normalized to a symbol and auditable source.

### AP-B03 Candidate Validity Gate

Purpose: remove rows that are structurally unsafe to evaluate.

Required checks:
- Symbol present.
- Candidate source present.
- Direction authority present.
- Price present.
- Non-stale artifact.
- Schema version compatible.

Output:
- `valid_candidate=true|false`
- `candidate_invalid_reason`

### AP-B04 AI2 Status Interpreter

Purpose: convert AI2 labels to machine decisions.

Mapping:

| AI2 status | Machine action |
|---|---|
| `Proceed candidate` | `CONTINUE` |
| `Review before execution` | `ENTER_REDUCED` or `REFRESH_AND_RECHECK` |
| `Do not execute until refreshed` | `REFRESH_AND_RECHECK` |
| Missing required AI2 enrichment | `BLOCK` |
| AI2 provider error | `REFRESH_AND_RECHECK` or `BLOCK`, based on freshness policy |

Audit:
- Preserve raw AI2 label.
- Preserve AI2 check notes.
- Preserve provider status without credentials.

### AP-B05 Warning Interpreter

Purpose: make warning handling deterministic.

Reference mapping:

| Warning | Default action |
|---|---|
| `high_volatility` | `ENTER_REDUCED` |
| `large_1d_move` | `REFRESH_AND_RECHECK` |
| `large_intraday_move` | `REFRESH_AND_RECHECK` |
| `weak_liquidity` | `BLOCK` if below hard floor, otherwise `ENTER_REDUCED` |
| `intraday_unavailable` | `REFRESH_AND_RECHECK` |
| `spread_too_wide` | `BLOCK` unless spread-edge policy allows reduced entry |
| `price_checks_clear` | `CONTINUE` |

### AP-B06 Refresh Gate

Purpose: prevent stale or chase-risk entries.

Inputs:
- Quote age.
- EOD date.
- Intraday timestamp.
- AI2 enrichment timestamp.
- Warning interpreter output.

Output:
- `refresh_required=true|false`
- `refresh_reason`
- `refresh_source`

Rule:
- If `refresh_required=true`, final entry decision cannot be `ENTER` or `ENTER_REDUCED` in the same pass.

### AP-B07 Tradability Gate

Purpose: enforce hard trading constraints.

Checks:
- Minimum price.
- Minimum market cap.
- Minimum average dollar volume.
- Session mode.
- Overnight tradability.
- Short-side policy.
- Existing held symbol.
- Existing open order.
- Anti-churn cooldown.
- Position-intent guard.

Output:
- `tradable=true|false`
- `tradability_block_reason`

### AP-B08 Risk Scoring Engine

Purpose: compute execution risk and quality.

Inputs:
- Validated expected return.
- Estimated transaction cost.
- Spread.
- Volatility tier.
- Liquidity tier.
- Risk tier.
- Portfolio exposure.
- Warning actions.

Outputs:
- `gross_edge_bps`
- `estimated_cost_bps`
- `net_edge_bps`
- `risk_score`
- `risk_score_reason`

### AP-B09 Position Sizing Engine

Purpose: determine paper size from approved risk.

Inputs:
- Account equity.
- Candidate risk score.
- Risk tier.
- Session multiplier.
- Validation caps.
- Warning action.

Outputs:
- `approved_notional`
- `suggested_quantity`
- `sizing_status`
- `sizing_reason`

Rule:
- `ENTER_REDUCED` must visibly reduce notional or explain why the minimum size is already reached.

### AP-B10 Entry Decision Engine

Purpose: combine AP-B03 through AP-B09 into one final decision.

Precedence:
1. Structural invalidity -> `BLOCK`
2. Hard tradability failure -> `BLOCK`
3. Refresh required -> `REFRESH_AND_RECHECK`
4. Hard risk failure -> `BLOCK`
5. Reduced warning or risk tier -> `ENTER_REDUCED`
6. Clean candidate -> `ENTER`

### AP-B11 Trade Intent Builder

Purpose: produce auditable intent rows.

Required outputs:
- `intent_id`
- `candidate_id`
- `symbol`
- `side`
- `decision`
- `decision_reason`
- `approved_notional`
- `suggested_quantity`
- `execution_allowed`
- `shadow_only`

### AP-B12 Execution Handoff

Purpose: transfer approved intents to the existing paper execution adapter only after promotion.

Shadow mode:
- `execution_allowed=false`
- `shadow_only=true`
- No broker call.

Future paper-active mode:
- Existing paper-only guards remain mandatory.
- Live trading remains disabled.

## L1-M02 Position Management Blocks

### PM-B01 Position Creation

Inputs:
- Filled order.
- Broker fill details.
- Original intent.

Outputs:
- `position_id`
- `trade_id`
- `entry_price`
- `entry_time`
- `entry_source`

### PM-B02 Initial Risk Attachment

Outputs:
- `initial_stop_loss`
- `initial_take_profit`
- `max_hold_days`
- `trailing_enabled`
- `risk_attachment_status`

### PM-B03 Live Mark-to-Market

Outputs:
- `last_price`
- `unrealized_pl`
- `unrealized_pl_pct`
- `peak_pl_pct`
- `giveback_pct`
- `quote_freshness_seconds`

### PM-B04 Stop-Loss Engine

Decision precedence:
- Hard stop hit -> `EXIT`
- Soft stop with weak evidence -> `REDUCE`
- No stop breach -> `HOLD`

### PM-B05 Profit-Taking Engine

Decision precedence:
- Take-profit hit with weak continuation -> `EXIT`
- Meaningful profit with mixed continuation -> `REDUCE`
- Meaningful profit with strong continuation -> `TRAIL`
- No profit event -> `HOLD`

### PM-B06 Trailing Stop Engine

Rules:
- Cannot trigger before the profit arm threshold.
- Must preserve `peak_pl_pct`.
- Must cite giveback threshold.

### PM-B07 Time-Based Exit Engine

Rules:
- At horizon, compare current candidate strength before forcing exit.
- If the symbol still qualifies, output `ROLL` or `HOLD`, not exit-and-reopen churn.

### PM-B08 Portfolio Risk Overlay

Outputs:
- `basket_state`
- `portfolio_risk_action`
- `risk_overlay_reason`

### PM-B09 Re-Entry / Add-On Logic

Rules:
- Prevent same-cycle close/open churn.
- Prevent immediate re-entry after close unless explicit roll rule passes.
- Permit `ADD` only when risk and position caps allow.

### PM-B10 Exit Decision Engine

Combines all position blocks.

Precedence:
1. Emergency risk breach -> `EXIT`
2. Hard stop -> `EXIT`
3. Portfolio risk overlay -> `REDUCE` or `EXIT`
4. Profit-taking / trailing -> `TRAIL`, `REDUCE`, or `EXIT`
5. Time horizon -> `HOLD`, `ROLL`, or `EXIT`
6. Otherwise -> `HOLD`

### PM-B11 Performance Attribution

Required attribution dimensions:
- Candidate source.
- AI2 status.
- Warning codes.
- Entry session.
- Exit reason.
- Holding duration.
- Gross P/L.
- Net estimated P/L.
- Slippage.
- Spread.

### PM-B12 Feedback Store

Stores:
- Intent outcome.
- Fill outcome.
- Position outcome.
- Counterfactual outcome.
- Gate and warning attribution.

The feedback store is read-only for trading behavior until a separate material-change ticket promotes its use.

