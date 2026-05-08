# CSV Column Contracts

## 04 metadata enriched

`data/interim/04_us_metadata_enriched_YYYYMMDD_HHMMSS.csv`

Required columns: ticker, company, exchange, sector, industry, market_cap, beta, trailing_pe, forward_pe, price_to_book, dividend_yield, average_volume, quote_type, currency, country, metadata_status, metadata_error.

`data/interim/04_us_metadata_quality_YYYYMMDD_HHMMSS.csv`

Required columns: ticker, metadata_status, metadata_error, metadata_missing_ratio, has_sector, has_market_cap.

## 05 feature panel

`data/processed/05_us_feature_panel_YYYYMMDD_HHMMSS.csv`

Required columns include identity, OHLCV, dollar volume, returns, moving averages, RSI, MACD, rolling highs/lows, volatility, liquidity, sector-relative return, market return, and feature_missing_ratio.

## 05 news sentiment panel

`data/processed/05_news_sentiment_panel_YYYYMMDD_HHMMSS.csv`

Required columns: date, ticker, article_count, sentiment_score_mean, sentiment_score_min, sentiment_score_max, sentiment_positive_count, sentiment_negative_count, sentiment_neutral_count, sentiment_source, sentiment_status.

Provider failures must be represented with status fields, not fabricated production sentiment.

## 06 Gold ML dataset

`data/gold/06_us_gold_ml_dataset_YYYYMMDD_HHMMSS.csv`

The Gold dataset is one row per ticker-date and combines metadata, OHLCV, technical indicators, liquidity, volatility, sector-relative features, market context, sentiment, candidate-selection scores, and ranking-first targets.

Target columns: target_return_5d, target_return_10d, target_sector_relative_return_5d, target_sector_relative_return_10d, target_rank_pct_by_date_5d, target_top_quintile_5d, target_bottom_quintile_5d, target_trade_label_5d.

Target and model-output columns must be excluded from model feature matrices.

## 07 portal outputs

Portal CSVs live under `data/portal_outputs`:

- `07_portal_signals_YYYYMMDD_HHMMSS.csv`
- `07_portal_dashboard_metrics_YYYYMMDD_HHMMSS.csv`
- `07_portal_sector_breakdown_YYYYMMDD_HHMMSS.csv`

## Advanced model outputs

Model outputs live under `data/model_outputs` and are generated from Gold only:

- `advanced_model_latest_predictions_YYYYMMDD_HHMMSS.csv`
- `advanced_model_signal_table_YYYYMMDD_HHMMSS.csv`
- `advanced_model_top_long_signals_YYYYMMDD_HHMMSS.csv`
- `advanced_model_top_short_signals_YYYYMMDD_HHMMSS.csv`
- `advanced_model_validation_leaderboard_YYYYMMDD_HHMMSS.csv`
- `advanced_model_confidence_bucket_performance_YYYYMMDD_HHMMSS.csv`
- `advanced_model_feature_importance_YYYYMMDD_HHMMSS.csv`
- `advanced_model_model_status_YYYYMMDD_HHMMSS.csv`
- `advanced_model_data_dictionary_YYYYMMDD_HHMMSS.csv`

If validation gates do not pass, `advanced_model_model_status_*` must set `decision_grade=diagnostic_only` and signal rows must remain `No Decision`.
