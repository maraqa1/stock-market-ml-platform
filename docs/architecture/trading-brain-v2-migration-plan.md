# Trading Brain V2 Migration Plan

Trading Brain V2 must be introduced without changing live execution behavior. The existing brain remains the active execution path until V2 passes shadow-mode validation and a separate promotion ticket explicitly changes routing.

## Branch Strategy

Use a dedicated feature branch:

```text
codex/trading-brain-v2
```

Branch rules:
- Keep documentation, shadow implementation, and activation changes in separate commits.
- Do not mix config activation with implementation.
- Do not merge generated CSVs, credentials, API keys, or local runtime outputs.
- Keep the first PR shadow-only.
- Promotion from shadow to active paper execution must be a later PR.

## Feature Flags

Proposed flags for future implementation:

```yaml
trading_brain_v2:
  enabled: false
  mode: shadow
  active_execution: false
  write_shadow_intents: true
  compare_to_existing_brain: true
  require_ai2_enrichment: false
  allow_live_trading: false
```

Allowed modes:
- `off`
- `shadow`
- `paper_candidate`
- `paper_active`

Initial required values:
- `enabled: false` or `mode: shadow`
- `active_execution: false`
- `allow_live_trading: false`

## Migration Phases

### Phase 0 - Documentation and Contract

Goal: freeze the target architecture before code changes.

Deliverables:
- Architecture document.
- Reference block contract.
- Migration plan.
- PR checklist.

Behavior change:
- None.

Rollback:
- Revert documentation commit only.

### Phase 1 - Shadow Schema and Intent Files

Goal: add V2 output contracts without changing active trading.

Deliverables:
- Shadow intent schema.
- Shadow candidate decision CSV.
- Shadow position decision CSV.
- Shadow diff report versus existing brain.

Behavior change:
- None.

Acceptance:
- Existing brain still submits, closes, and manages positions exactly as before.
- V2 only writes audit artifacts.

Rollback:
- Disable `write_shadow_intents`.
- Existing active path is unaffected.

### Phase 2 - Candidate Path Shadow Evaluation

Goal: evaluate AP-B01 through AP-B12 in shadow mode.

Deliverables:
- Candidate normalization report.
- AI2 status interpretation report.
- Warning interpretation report.
- Entry intent report.
- Existing-brain versus V2 candidate diff.

Behavior change:
- None.

Acceptance:
- `REFRESH_AND_RECHECK` rows never appear as executable V2 intents.
- Review-like AI2 labels map only to `ENTER_REDUCED`, `REFRESH_AND_RECHECK`, or `BLOCK`.
- No V2 order submission occurs.

Rollback:
- Disable V2 candidate shadow job.

### Phase 3 - Position Management Shadow Evaluation

Goal: evaluate PM-B01 through PM-B12 in shadow mode.

Deliverables:
- Position decision report.
- Exit reason attribution report.
- Existing position-manager versus V2 diff.
- Re-entry/add-on/churn report.

Behavior change:
- None.

Acceptance:
- No V2 close, reduce, add, or roll action reaches broker.
- No manual review state appears.

Rollback:
- Disable V2 position shadow job.

### Phase 4 - Paper Candidate Mode

Goal: allow V2 to produce paper intents eligible for review by the existing execution handoff, without submitting them.

Behavior change:
- Still no V2 broker submission.
- Existing brain remains active.

Acceptance:
- Intent counts reconcile with shadow reports.
- No duplicate order risk.
- Existing brain output remains unchanged.

Rollback:
- Return mode to `shadow`.

### Phase 5 - Paper Active Promotion

Goal: switch paper execution ownership to V2 after explicit approval.

This is a material change and requires:
- New segment pre-registration.
- Config fingerprint update.
- Rerun-diff report.
- Paper-only guard confirmation.
- Rollback plan tested.

Behavior change:
- V2 becomes paper execution owner.
- Live trading remains disabled.

Rollback:
- Set `trading_brain_v2.active_execution=false`.
- Restore existing `execution_owner: paper_autopilot`.
- Restart scheduler/portal services.

## Rollback Strategy

Every phase must support rollback by config, not code deletion.

Minimum rollback controls:
- Disable V2 shadow writer.
- Disable V2 active execution.
- Restore existing execution owner.
- Stop V2 scheduled jobs.
- Preserve audit artifacts for postmortem.

Rollback must not:
- Delete generated audit files.
- Modify existing broker orders.
- Enable live trading.
- Override safety guards.

## Audit Requirements

V2 must write audit records for:

- Input artifacts and mtimes.
- Candidate normalization.
- AI2 interpretation.
- Warning interpretation.
- Refresh decisions.
- Tradability decisions.
- Risk scoring.
- Position sizing.
- Entry decisions.
- Trade intents.
- Position-management decisions.
- Existing brain comparison.
- Any blocked execution handoff.

Minimum audit fields:
- `brain_version`
- `shadow_mode`
- `strategy_version`
- `cycle_id`
- `symbol`
- `block_id`
- `decision`
- `decision_reason`
- `input_artifact_path`
- `config_fingerprint`
- `created_at`

## Testing Strategy

### Unit Tests

Required coverage:
- AI2 status mapping.
- Warning mapping.
- Refresh-required cannot execute.
- Review-like states map to deterministic machine actions.
- Candidate validity failures block.
- Position-management decisions contain no manual review.
- Sizing reduction is explicit and auditable.
- Live trading flag is never enabled.

### Integration Tests

Required coverage:
- V2 shadow reads latest candidate file.
- V2 shadow reads latest positions/tracking.
- V2 writes intents without broker submission.
- Existing brain output is unchanged when V2 shadow is enabled.
- Existing paper-only guards remain intact.

### Regression Tests

Required coverage:
- Current `paper_autopilot.tick()` behavior unchanged in shadow mode.
- Existing `execution_ranked_auto_open_candidates()` behavior unchanged.
- Existing position management outputs unchanged.
- No generated CSV committed.

### End-to-End Shadow Test

Run during market session and after-hours:
- Existing brain operates normally.
- V2 emits shadow decisions.
- Diff report is populated.
- No V2 broker order ID is created.

## PR Checklist

Before merging any Trading Brain V2 PR:

- [ ] Source code changes are limited to the stated phase.
- [ ] Live trading remains disabled.
- [ ] Existing active execution path is unchanged unless this is an approved paper-active promotion PR.
- [ ] No manual review final state exists in V2 output.
- [ ] Review-like inputs map to `ENTER_REDUCED`, `REFRESH_AND_RECHECK`, or `BLOCK`.
- [ ] Refresh-required rows cannot produce executable intents.
- [ ] Shadow mode writes audit files only.
- [ ] Tests pass.
- [ ] Rerun-diff confirms no active execution behavior change.
- [ ] Config fingerprint impact is documented.
- [ ] Rollback steps are documented.
- [ ] Generated CSV/Markdown outputs are not committed.
- [ ] Credentials and API keys are not committed.

## Recommended Initial Insertion Point

The first implementation should attach after the latest candidate artifact is available and before existing open-order submission:

```text
execution_ranked_candidates_*.csv
  -> Trading Brain V2 shadow candidate blocks
  -> shadow trade intents
  -> existing brain continues unchanged
```

For position management, attach after the latest broker tracking and positions snapshot:

```text
08_alpaca_paper_positions_*.csv
08_alpaca_paper_order_tracking_*.csv
  -> Trading Brain V2 shadow position blocks
  -> shadow position decisions
  -> existing position management continues unchanged
```

## Promotion Gate

V2 may not become active until:

- Shadow decisions are stable.
- Diff reports are understood.
- Paper-only execution is proven.
- Audit files reconstruct every decision.
- Rollback has been tested.
- A material-change review approves paper-active mode.

