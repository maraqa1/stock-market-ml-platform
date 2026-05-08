# Gold Dataset Definition

The Gold dataset is the central model-ready panel for the stock intelligence platform.

Grain: one row per ticker and trading date.

It combines:

- identity and metadata
- OHLCV price history
- technical, momentum, liquidity, volatility, sector-relative, and market-context features
- timestamp-safe news sentiment features when available
- candidate-selection scores and ranks
- ranking-first supervised targets

The primary modelling objective is not raw return regression. The dataset supports ranking stocks by likelihood of top-quintile or bottom-quintile forward 5-day performance relative to the tradable universe and sector.

All target columns are excluded from model features.

Downstream model training and prediction must read the latest Gold dataset directly from `data/gold/06_us_gold_ml_dataset_*.csv`. The model layer must not train from raw, interim, feature-panel, sentiment, or portal output files.
