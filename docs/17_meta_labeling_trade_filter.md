# Meta-Label Trade Filter

The primary ranking model still decides direction: `Long`, `Short`, or `No Decision`.

The meta-label model is a second-stage filter. It only looks at historical `Long` and `Short` signals and learns whether taking the trade was worthwhile after transaction cost.

## Target

For each historical primary trade signal:

- Long realized gain: `target_return_5d - transaction_cost`
- Short realized gain: `-target_return_5d - transaction_cost`
- `meta_label = 1` when realized gain is positive
- `meta_label = 0` otherwise

`No Decision` rows are not accepted as positive trade examples.

## Feature Rules

The meta-label model only uses pre-trade, decision-time features such as confidence, probability edge, rank, expected return, risk score, liquidity, volatility, market cap, regime, sentiment, and tier labels.

It explicitly excludes leakage fields:

- `target_*`
- `future_*`
- `realized_*`
- `pnl_*`
- order, fill, and execution result fields
- `trade_action`
- validation, test, and fold indicators

## Validation

Validation is walk-forward only with an embargo. There is no random split.

Reported metrics include:

- accuracy
- precision
- recall
- F1
- ROC AUC
- Brier score
- accepted trade hit rate
- accepted trade average realized gain
- skipped trade avoided-loss estimate

## Decision Gate

A trade may proceed only when:

- the primary model says `Long` or `Short`
- the primary model is decision-grade
- `meta_label_probability` is at or above the configured threshold
- expected trade return is above transaction cost
- trade quality and risk gates pass

The order planner enforces the meta-label gate when `meta_label_probability` is present in the signal table. This preserves compatibility with older signal artifacts before the next model run writes meta-label columns.

## Outputs

The model output job writes:

- `data/model_outputs/meta_label_predictions_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/meta_label_validation_YYYYMMDD_HHMMSS.csv`
- `data/model_outputs/meta_label_bucket_performance_YYYYMMDD_HHMMSS.csv`

Signal and order outputs include:

- `meta_label_probability`
- `meta_label_decision`
- `meta_label_reason`
