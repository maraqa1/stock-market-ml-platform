# AI2 Extended Enrichment Diagnostics

Trading Brain V2 can now preserve optional AI2 enrichment fields beyond the original shortlist contract. The fields are read-only metadata for now; they do not change candidate selection, sizing, order submission, or position management.

Optional fields currently supported by the V2 candidate normalizer:

- `ai2_realtime_price`, `ai2_quote_timestamp`, `ai2_quote_age_seconds`
- `ai2_sma_20`, `ai2_sma_50`, `ai2_rsi_14`, `ai2_atr_14`
- `ai2_news_count`, `ai2_sentiment_score`, `ai2_news_attention_score`
- `ai2_exchange`, `ai2_currency`, `ai2_security_type`

Run the diagnostic:

```bash
PYTHONPATH=src /opt/jupyter-env/bin/python3 scripts/run_ai2_extended_enrichment_diagnostics.py
```

Outputs:

- `data/trading/diagnostics/ai2_extended_enrichment_YYYYMMDD_HHMMSS.csv`
- `data/trading/diagnostics/ai2_extended_enrichment_YYYYMMDD_HHMMSS.md`

The diagnostic reports coverage for real-time quote, technical, news/sentiment, and exchange identity groups. It also emits shadow-only recommendations such as `would_refresh_quote`, `would_review_technical`, and `would_review_news`.

Safety boundary: these recommendations are not execution gates. Any future use in the trading brain must be added through a separate ticket with tests and a materiality review.
