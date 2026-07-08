# Gold Dataset Definition

The Gold v2 decision daily dataset is the central model-ready panel for the stock intelligence platform.

Grain: one row per ticker and trading date.

It combines:

- identity and metadata
- OHLCV price history
- technical, momentum, liquidity, volatility, sector-relative, and market-context features
- timestamp-safe news sentiment features when available
- candidate-selection scores and ranks
- ranking-first supervised targets

The primary modelling objective is not raw return regression. The dataset supports ranking stocks by likelihood of top-quintile or bottom-quintile forward 5-day performance relative to the tradable universe and sector.

Primary model target:

- `target_rank_pct_by_date_5d`

Supporting targets:

- conservative Long/Short/Neutral labels
- tiered Strong/Weak Long/Short labels
- volatility-adjusted forward return
- decay-weighted forward return

All target columns are excluded from model features.

Downstream model training, prediction, candidate selection, and trading diagnostics must read the latest Gold v2 master dataset from `data/gold/gold_stock_decision_daily_*.csv`.

The legacy `data/gold/06_us_gold_ml_dataset_*.csv` output is still written for backward compatibility and as the source used to build Gold v2. It must not be the default trading/model input when a matching Gold v2 decision daily file exists, because Gold v2 carries the ticker-level direction-memory fields required by the direction gate and candidate evidence diagnostics.

The profile pipeline therefore writes both:

- `06_us_gold_ml_dataset_*.csv`: compatibility/source artifact.
- `gold_stock_decision_daily_*.csv`: master model and trading artifact.

The model layer must not train from raw, interim, feature-panel, sentiment, or portal output files.
