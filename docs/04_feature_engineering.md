# Feature Engineering

Feature panel output:

`data/processed/05_us_feature_panel_YYYYMMDD_HHMMSS.csv`

Feature calculations use past and current rows only:

- returns: 1, 5, 10, 20, and 60 trading days
- technicals: 20/50/200-day simple moving averages, SMA gaps, RSI 14, MACD
- liquidity: dollar volume, 20-day average dollar volume, volume ratio
- volatility: 20/60-day volatility, downside volatility, 60-day drawdown
- sector context: sector median returns and ticker-relative performance
- market context: market median returns, volatility, regime score, risk flag

The feature panel is deterministic and testable from synthetic OHLCV data.
