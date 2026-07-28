# Daily Trading Single-Brain Authority

Daily paper trading must have one execution decision authority. The active owner is configured in `config/autopilot.yaml`:

```yaml
execution_owner: paper_autopilot
daily_trading_authority:
  enabled: true
  decision_owner: paper_autopilot
  allow_auto_rotations: false
  allow_fallback_candidate_brains: false
  allow_legacy_basket_submit: false
```

`paper_autopilot` is the only automated path allowed to submit daily paper open, close, reduce, or increase actions. Other modules may still produce diagnostics, research files, portal rows, and counterfactual evidence, but they must not become independent order brains.

## Blocked Secondary Brains

- Legacy basket submitter: blocked unless `allow_legacy_basket_submit` is explicitly enabled.
- Auto-rotation engine: blocked unless `allow_auto_rotations` is explicitly enabled.
- Fallback candidate brains: blocked unless `allow_fallback_candidate_brains` is explicitly enabled.

The block reason is:

```text
daily_trading_single_brain_blocks_secondary_decision_path
```

## Intended Flow

1. Nightly/intraday jobs build research data and candidate evidence.
2. Execution-ranked candidates define what Paper Autopilot may open.
3. Position-management decisions define what Paper Autopilot may hold, close, reduce, or increase.
4. Paper Autopilot applies the action with anti-churn, session, lifecycle, and paper-only guards.

This keeps diagnostics rich while preventing daily trading from being controlled by several overlapping decision engines.
