# Statistical Validity And Governance Review

This document is the research-governance standard for the StockML ranking engine. It is intentionally stricter than the paper-trading layer. A model can produce interesting research candidates and still fail the requirements below.

## 1. Statistical Validity

Observed alpha must be treated as statistical noise until proven otherwise.

Key risks:

- Multiple testing: repeated feature additions, threshold tuning, and model comparisons inflate false discoveries.
- Feature mining: many technical, sector, market, and sentiment features increase the chance of accidental fit.
- P-hacking: changing ICIR, rank, confidence, or liquidity thresholds after reviewing outcomes contaminates validation.
- Data snooping: using today's universe, today's metadata, or corrected historical data can make history look easier than live trading.
- IC instability: a positive average IC is not enough if it clusters in one period, sector, or regime.
- Economic weakness: a statistically positive IC can still be too small after costs, slippage, borrow constraints, and turnover.

Required checks:

- Confidence interval around mean daily IC and ICIR.
- Fold-level IC distribution, not only overall IC.
- Positive IC in at least 3 of 4 folds.
- Sensitivity to transaction costs and turnover.
- Baseline comparison against simple return_20d, return_60d, and sector-relative momentum.
- Report whether sample size is sufficient by date count, ticker count, and number of independent validation periods.

Metrics likely to predict live performance:

- Persistent daily rank IC across regimes.
- Net long-short spread after turnover costs.
- Fold stability.
- Drawdown of simulated long-short portfolio.
- Performance by liquidity bucket.

Metrics likely to mislead:

- Single-period hit rate.
- In-sample feature importance.
- Raw classifier probability without calibration.
- Top-N return before costs.
- Overall accuracy on an imbalanced classification target.

Expected live degradation:

- Alpha should be haircut materially before any capital decision.
- Paper results should be degraded for transaction cost, latency, slippage, partial fills, and missed opens.
- Any edge that disappears under 10 to 25 bps round-trip cost should not be considered deployable.

## 2. Cross-Sectional Quant Design

The system should behave like institutional cross-sectional research, not a collection of ticker predictions.

Required reviews:

- Factor exposure overlap: measure correlation with momentum, volatility, beta, size, liquidity, and sector factors.
- Hidden momentum bias: prove the model is not just rediscovering return_20d or return_60d.
- Hidden beta bias: check whether Longs have systematically higher market beta than Shorts.
- Hidden sector bias: report sector exposure of top and bottom ranks.
- Hidden market-cap bias: report rank performance by market-cap bucket.
- Rank stability: measure turnover and rank autocorrelation.
- Signal orthogonality: compare model scores against baseline factor scores.

Alpha uniqueness test:

- The model should beat simple momentum and sector-relative momentum out-of-sample.
- If equal-weight momentum performs similarly, the model is not unique.
- If gains concentrate in crowded high-momentum names, expect faster decay.

Holding-period realism:

- A 5-day horizon is plausible only if rank IC and long-short spread survive realistic turnover costs.
- If the signal requires same-day execution from daily bars, intraday validation is required before live trading.

## 3. Feature Lifecycle And Temporal Integrity

Every feature must be available at prediction time.

Audit requirements:

- Rolling windows must use only current and past observations.
- Same-date sector and market features must not use future returns or future constituents.
- Metadata such as market cap and beta must be point-in-time or explicitly treated as approximate.
- Sentiment timestamps must be aligned to article publication time and market close cutoff.
- Missing-value filling must not use future values.
- Revised or restated fields must be avoided unless point-in-time snapshots are available.

High-risk areas:

- Sector average returns if computed over target windows.
- Universe membership if defined from today's surviving tickers.
- Yahoo metadata fields if downloaded today and applied historically.
- Sentiment rows if articles are attached to dates after the prediction cutoff.

Required output:

- Feature audit table with inclusion status, exclusion reason, missing rate, and leakage risk.
- Rejected feature table.
- Point-in-time caveat for every non-point-in-time source.

## 4. Research Process

The process must reduce overfitting pressure.

Rules:

- No random train/test split for time-ordered market data.
- Use expanding walk-forward validation.
- Keep a final untouched holdout period for model promotion.
- Do not tune thresholds repeatedly on the same validation period.
- Do not add features without an economic hypothesis.
- Compare every model against simple baselines before promotion.
- Retire models when IC, spread, drawdown, or turnover gates degrade beyond thresholds.

Recommended workflow:

1. Define economic hypothesis.
2. Add point-in-time safe features.
3. Run feature audit.
4. Train on historical expanding windows.
5. Validate on future folds.
6. Compare against baselines.
7. Freeze thresholds.
8. Evaluate on untouched holdout.
9. Run paper trading for at least 60 trading days.
10. Promote only after governance approval.

Retraining:

- Daily scoring is acceptable.
- Full retraining should be scheduled and versioned, not continuously hand-tuned.
- Any change to targets, features, thresholds, or validation gates creates a new model version.

## 5. Execution Realism

Daily bars are not sufficient proof of executable alpha.

Review requirements:

- Open versus close execution assumptions.
- Overnight gap risk.
- Queue priority and partial fill assumptions.
- Market impact for small-cap or thinly traded names.
- Slippage by liquidity bucket.
- Borrow constraints for Shorts.
- Intraday spread and volatility around entry time.

The 5-day horizon survives only if:

- Expected long-short spread is materially larger than costs.
- Turnover is not excessive.
- The strategy does not depend on impossible open-price fills.
- The signal remains valid after delayed execution.

## 6. Institutional Benchmark Comparison

Current expected maturity:

- Stronger than hobby-grade because it has Gold data, leakage controls, walk-forward validation, paper trading, and audit artifacts.
- Better than many retail quant systems because it is ranking-first and risk-gated.
- Not institutional-grade yet because point-in-time data, untouched holdout governance, alpha orthogonality, execution simulation, and live risk controls are incomplete.

Likely category today:

- Advanced retail to early research-grade.

Likely real-world outcome without further hardening:

- Paper trading survival: moderate.
- Live deployment survival: low to moderate.
- Sustained alpha after costs: low until the model beats baselines with stable net spreads.

## 7. Deployment Governance

Minimum safeguards before real money:

- Immutable model versioning.
- Reproducible training configuration.
- Prediction lineage from Gold file to signal to order.
- Rollback to previous model and previous execution config.
- Alerting for missing data, stale signals, API errors, drawdown, and exposure breaches.
- Paper-trading validation for at least 60 trading days.
- Capital ramp-up procedure.
- Automatic stop when drawdown, IC degradation, turnover, or execution errors breach limits.

Suggested stop conditions:

- Paper or live drawdown exceeds approved threshold.
- Rolling IC turns negative.
- Net long-short spread turns negative after cost.
- Model no longer beats return_20d and return_60d baselines.
- Data freshness or lineage is broken.
- Alpaca/API errors remain unresolved.

## 8. Explainability And Trust

Explanations must not overstate certainty.

Trustworthy explanations:

- Rank position.
- Sector-relative rank.
- Liquidity bucket.
- Validation status.
- Baseline comparison.
- Regime and sector performance slice.

Use caution with:

- SHAP values in correlated feature spaces.
- Raw feature importance.
- Raw classifier probabilities.
- Post-hoc explanations that change frequently.

Never present as a trade reason:

- A single unstable feature.
- Uncalibrated probability alone.
- In-sample importance.
- A composite score whose components were not audited.

## 9. Final Verdict

Top fatal weaknesses:

1. No untouched final holdout governance yet.
2. Point-in-time integrity is incomplete for metadata and universe membership.
3. Current model can fail ICIR and produce no decision-grade candidates.
4. LightGBM availability is optional rather than guaranteed in deployment.
5. Alpha uniqueness versus simple momentum is not yet proven.
6. Execution assumptions are still too simple for live trading.
7. Sentiment coverage and timestamp alignment need stronger audit.
8. Feature count can grow faster than economic rationale.
9. Paper trading duration is not yet sufficient.
10. Explainability is not yet stable enough for portfolio-manager trust.

Top strongest design decisions:

1. Ranking-first objective.
2. Default No Decision behavior.
3. Walk-forward validation.
4. Feature leakage exclusion.
5. Trade quality gate before orders.
6. Paper-only execution controls.
7. Position monitoring and P&L tracking.
8. Separation of signal generation from execution.
9. Database persistence for large outputs.
10. Explicit model-status and artifact outputs.

Top immediate fixes:

1. Install and standardize LightGBM in the VM environment.
2. Add untouched holdout evaluation.
3. Add point-in-time universe and metadata caveats/replacements.
4. Add factor exposure and orthogonality reports.
5. Add transaction-cost and slippage sensitivity reports to model validation.

Do not build yet:

1. Reinforcement learning.
2. Complex ensemble stacking.
3. Live trading.
4. Options/futures expansion.
5. Fully automated position closing with real capital.

Final classification:

- Current state: advanced retail / early research-grade.
- Target after the above hardening: small-fund-grade research system.
- Not yet institutional-grade.

Probability estimates:

- Surviving paper trading: moderate if strict gates remain.
- Surviving live deployment: low until execution and governance improve.
- Sustaining alpha after costs: unproven and likely low until baseline-adjusted, cost-adjusted validation is consistently positive.
