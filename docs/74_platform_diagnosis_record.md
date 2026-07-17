# Platform Diagnosis Record

**Date:** 2026-07-17
**Scope:** US equity cross-sectional trading platform (stockml), repo state ~379cdf5
**Method:** Iterative expert review of raw artifacts (Gold rows, candidate exports, execution tables), developer self-audits, module inventory, and item-level implementation verification.
**Status:** Diagnosis phase CLOSED. Execution phase begins per one-week plan (§7).

---

## 1. Signal & Economics — Findings

| Finding | Evidence | Status |
|---|---|---|
| Long-side gross edge is thin: ~42 bps, hit rate 48.4%, PF 1.156 | validated_* metrics in candidate exports | Confirmed, pre-PIT (likely optimistic) |
| Short leg is net-negative: PF 0.877, −29.7 bps | validated_* short-side metrics | Confirmed; gates already block it |
| Validated metrics are global side-level constants, not per-ticker | expected_return_scope = "side" on Long rows | Confirmed; per-ticker plumbing exists (scope="ticker" on shorts) but unpopulated for longs |
| Volatility-opportunity gate is circular: portfolio-average edge used to justify extreme-vol outlier trades | CRNX/ECHO admitted via volatility_extreme_offset_by_validated_edge | Confirmed |
| Expected-return scoring inflated by small-denominator division | +2,249% expected returns on micro-caps in top-200 export | Confirmed; no winsorization on vol-adjusted quantities |
| Source model abstains on ~90–95% of universe; earlier export synthesized directions from side field | source_trade_action = No Decision / NONE on most rows | Confirmed; shadow lane now separates these correctly |
| Ticker direction memory has no statistical edge (~0.48–0.55 confidence) yet acts as hard block | direction_memory_conflict blocks at conf ≈ 0.42–0.51 | Confirmed; needs confidence threshold or soft-penalty downgrade |

## 2. Data Integrity — Findings

| Finding | Evidence | Status |
|---|---|---|
| Survivorship bias: universe = current symbols backfilled; delisted names missing | Developer audit (self-reported) | Confirmed; invalidates historical backtests |
| Fundamentals not point-in-time: current snapshot merged across history | AMZN 2018 row with ~$2.86T market cap | Confirmed; market_cap/beta/sector/industry affected |
| Back-adjusted prices embed future corporate actions | adj_close vs close 20× gap (2022 split on 2018 row) | Ratio features/targets unaffected (factor cancels); level-dependent features need audit |
| Sentiment imputed neutral 0.5 with status flag | sentiment_status = unavailable | Acceptable; flag must be fed to model as categorical |
| Pipeline not deterministic for past as-of dates | Developer audit | Confirmed; provider state fetched at run time |
| Data quality mixed into alpha: selection_score averages data_quality/history_quality with signals | selection_score formula | Confirmed; quality scores should gate, not rank |

## 3. Platform Capability Inventory (verified)

**Exists and active:** universe/liquidity/price gates (values env-driven — verify), short-side candidate policy, position sizing framework (risk-tier, notional caps, sector concentration, gross/net limits), basket drawdown pause + risk overlay, Alpaca paper integration with lifecycle IDs and fill reconciliation, closed-trade and profitability attribution, pipeline doctor / monitoring diagnostics, walk-forward validation (ranker expanding, meta-label embargoed, same-day folds), cost/spread estimation (spread_edge), calibration modules (bucket + isotonic).

**Partial:** vol-extreme exclusion (vol-opportunity path admits extreme), net-of-cost ranking (costs computed, not in ranking sort — sorts raw_rank), counterfactual candidate logging (pool CSVs persist with prices; no forward-return contract), holding-period configurability (config exists, no 5/10/15d experiment), PIT universe/fundamentals (modules exist, fix unproven), purged-CV generality, roll-at-horizon logic (anti-churn exits + cooldown only).

**Missing:** config fingerprint / forward-paper manifest, event-driven backtest simulator, formal experiment registry, external SRE alerting, attribution→gate closed feedback loop.

**Latent risk:** broker short flag enabled on VM while shorts blocked only at candidate policy level — single-point-of-failure exposure on a leg known to lose money.

## 4. Approved Specs (v2 revisions required)

**PIT Data Foundation** — approved with 6 additions: (0) named data provider decision [BLOCKER — Norgate/Sharadar/EODHD-delisted]; (1) restatement rule: first-reported values preserved, restatements as new rows; (2) corporate-action/ticker-history table on stable ID (FIGI/CUSIP); (3) PIT price handling or explicit deferral; (4) PIT sector/industry source; (5) §7 retrain-and-measure with named regression tickers (HTZ, SIVB, FRC, CTXS, WORK) and hashed determinism checks.

**Forward Paper Program** — approved with 9 additions, two non-negotiable: (1) pre-registration of expected metric ranges before day 1; (2) counterfactual logging of full candidate pool with decision-time prices. Plus: benchmark/regime context, segmentation semantics (segments never merged), named change controller + shadow lane, mechanical materiality test (rerun-N-days diff), operational failure states, declared capital base, monthly tearsheet.

## 5. Commercial Position (decided)

- **Product A (platform)** and **Product B (expertise/consulting)** are sellable now; differentiation = engineering honesty and execution discipline, not returns.
- **Product C (alpha/signals/managed)** gated behind: PIT rebuild + 6–12 months clean forward paper + regulatory path (FCA perimeter review before any UK marketing of C).
- Hard rule: no performance claims anywhere until C-gate passes. The self-blocked short leg and the survivorship self-audit are sales assets, not embarrassments.

## 6. Profitability Thesis

Profit = gross edge (thin, ~42 bps) − costs (10–25 bps liquid, 30–80 bps small/volatile). Near-term profitability is therefore **subtraction**: kill shorts, floor the universe, rank net-of-cost, cut turnover, stand down in hostile regimes. Signal improvement (calibrated sizing, meta-label filter, orthogonal features) is sequenced AFTER PIT-honest measurement. Month-6 decision gate: positive stable net edge in paper → scale toward C; zero → pivot signal family on same platform.

## 7. One-Week Execution Plan (agreed)

| Day | Action | Type |
|---|---|---|
| 1 | Verify+raise floors ($500M cap / $20M ADV / $5 price); hard-exclude extreme vol; disable broker-level shorting | Config |
| 1–2 | Build config_fingerprint + minimal daily manifest | Code (small) |
| 2–3 | Net-of-cost ranking in execution_ranker (sort on gross − per-name cost) | Code (main) |
| 3 (eve) | FREEZE config → forward paper segment 1 starts with all above included | Process |
| 4 | Counterfactual contract on candidate pool export; 5d/10d/15d net-of-cost holding notebook (pre-PIT, indicative) | Code + research |
| 5 | Honest one-pager (capabilities, limitations, this week's changes, 90-day plan) | Document |

**Deferred (queued as segment-2+ material changes):** roll-at-horizon logic (input: holding-period results), per-ticker validated edges, calibration coverage stabilization, direction-memory confidence threshold, PIT §0 provider decision → phases 1–5, rank-column consolidation, "approved" token rename in all_block_reasons.

## 8. Standing Principles (for future reviews)

1. Lower Sharpe on honest data is progress, not regression.
2. Module existence ≠ production-path execution — every audit must prove the daily path.
3. Averages hide the edge — attribution cuts (tier/liquidity/regime/signal-decile) decide what trades.
4. Material changes segment the evidence clock; segments never merge.
5. Subtraction before addition: stop losing before trying to win more.