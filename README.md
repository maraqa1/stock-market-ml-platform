# Stock Market ML Platform

Research-grade student stock-market machine learning platform for building a broad US equity universe, engineering Gold ML datasets, validating ranking models, and exposing Long / Short / No Decision outputs through a web portal.

This project is for education, experimentation, and model research. It is not financial advice and must not be treated as an automated trading system.

## 1. Purpose

The platform solves one core problem: a stock model cannot generate useful signals if it only sees a small universe of stocks.

The platform expands the current prototype into a full research pipeline with:

- broad US equity universe construction
- tradable-stock filtering
- Yahoo Finance OHLCV history
- metadata enrichment
- technical, liquidity, volatility, sector, and market features
- target engineering
- Gold ML dataset generation
- intelligent candidate selection
- walk-forward model validation
- ranking-first Long / Short / No Decision outputs
- portal-ready CSV files

## 2. Safety Boundary

This repository is isolated from the current running Yahoo student platform.

It does not modify:

- /home/massa/notebooks/yahoo_sector_data
- /usr/local/bin/build-yahoo-advanced-trading-model
- /opt/yahoo-student-portal/app.py

All new files are written under:

- /home/massa/stock-market-ml-platform/data

## 3. Target Pipeline

Full US equity universe
-> Tradable universe filter
-> Yahoo Finance price history
-> Metadata enrichment
-> Feature panel
-> Target engineering
-> Gold ML dataset
-> Candidate-selection layer
-> Ranking-first ML model
-> Walk-forward validation
-> Decision logic
-> Portal-ready outputs

## 4. Repository Structure

- config: YAML configuration
- data: local data folders ignored by git
- docs: design and operating documentation
- scripts: pipeline runners
- src/stockml: main Python package
- builders: direct VM builder scripts
- portal: future isolated portal code
- deployment: systemd, nginx, and VM assets
- notebooks: research notebooks
- tests: validation tests
- outputs_contract: CSV schema contracts

## 5. Output Contract

The model layer must produce:

- advanced_model_latest_predictions_YYYYMMDD_HHMMSS.csv
- advanced_model_signal_table_YYYYMMDD_HHMMSS.csv
- advanced_model_top_long_signals_YYYYMMDD_HHMMSS.csv
- advanced_model_top_short_signals_YYYYMMDD_HHMMSS.csv
- advanced_model_validation_folds_YYYYMMDD_HHMMSS.csv
- advanced_model_validation_leaderboard_YYYYMMDD_HHMMSS.csv
- advanced_model_confidence_bucket_performance_YYYYMMDD_HHMMSS.csv
- advanced_model_feature_importance_YYYYMMDD_HHMMSS.csv
- advanced_model_model_status_YYYYMMDD_HHMMSS.csv
- advanced_model_data_dictionary_YYYYMMDD_HHMMSS.csv

The main portal-facing file is advanced_model_signal_table_YYYYMMDD_HHMMSS.csv.

Valid trade_action values are:

- Long
- Short
- No Decision

## 6. Modelling Rules

1. No random train/test split for market time-series data.
2. Walk-forward validation only.
3. No target columns may be used as model features.
4. No future or realized outcome columns may be used as model features.
5. The model must beat baseline before signals are trusted.
6. The default output is No Decision.
7. Fewer high-quality signals are preferred over many weak signals.
8. Exact short-term return regression is not the main decision engine.
9. Expected return should be estimated from validated historical bucket performance.
10. Signal quality must be checked by confidence bucket, sector, date period, and realized gain.

## 7. Candidate Selection

Keep common stocks, active tickers, NYSE/NASDAQ/NYSE American listings, valid Yahoo Finance history, valid metadata, sufficient liquidity, sufficient history, reasonable volatility, and acceptable missing-data profile.

Exclude ETFs, funds, warrants, rights, units, preferred shares, bonds, SPAC units, illiquid microcaps, broken tickers, short-history securities, and extreme missing-data cases.

## 8. Validation Metrics

Required metrics include ROC AUC, macro F1, balanced accuracy, Long precision, Short precision, signal hit rate, average realized gain, median realized gain, top-decile signal performance, confidence bucket performance, sector concentration, turnover, drawdown, and transaction-cost sensitivity.

## 9. Main Commands

- python scripts/run_universe_pipeline.py
- python scripts/run_gold_pipeline.py
- python scripts/run_model_pipeline.py
- python scripts/run_full_pipeline.py

Expected VM execution:

- /opt/jupyter-env/bin/python3 scripts/run_full_pipeline.py

## 10. Python Environment

Default VM Python:

- /opt/jupyter-env/bin/python3

Install dependencies:

- /opt/jupyter-env/bin/python3 -m pip install -r requirements.txt
- /opt/jupyter-env/bin/python3 -m pip install -r requirements-optional.txt

## 11. Git and Data Rules

Large data files must not be committed. Data directories are kept in git only through .gitkeep files.

Do not commit data/raw, data/interim, data/processed, data/gold, data/model_outputs, data/portal_outputs, .env, or logs.

## 12. Research Limitation

This platform does not guarantee trading performance. Market prediction is noisy, unstable, regime-dependent, and sensitive to data quality, transaction costs, liquidity, and survivorship bias.

Any model output must be interpreted as a research signal, not investment advice.
