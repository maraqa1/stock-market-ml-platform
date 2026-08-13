from __future__ import annotations

import pandas as pd

from stockml.diagnostics.ai2_extended_enrichment import (
    enrich_with_ai2_extended_diagnostics,
    run_ai2_extended_enrichment_diagnostics,
)


def test_missing_extended_fields_are_reported_without_changing_candidate_status(tmp_path):
    source = tmp_path / "ai2_enriched_execution_ranked_candidates_20260813_120000.csv"
    pd.DataFrame(
        [
            {
                "symbol": "GCT",
                "status": "executable",
                "execution_domain": "execution_candidate",
                "executable": True,
                "order_eligible": True,
            }
        ]
    ).to_csv(source, index=False)

    result = run_ai2_extended_enrichment_diagnostics(candidate_file=source, output_dir=tmp_path, stamp="20260813_120000")
    detail = pd.read_csv(result.detail_path)

    assert result.status == "ok"
    assert result.group_coverage["realtime_quote"] == 0
    assert result.missing_columns["technical"] == ["ai2_sma_20", "ai2_sma_50", "ai2_rsi_14", "ai2_atr_14"]
    assert detail.loc[0, "status"] == "executable"
    assert bool(detail.loc[0, "executable"]) is True
    assert detail.loc[0, "ai2_extended_enrichment_recommendation"] == "no_extra_enrichment_available"


def test_extended_fields_flag_stale_quotes_technical_and_news_risk():
    frame = pd.DataFrame(
        [
            {
                "symbol": "CLEAN",
                "ai2_realtime_price": 42.0,
                "ai2_quote_timestamp": "2026-08-13T12:00:00Z",
                "ai2_quote_age_seconds": 30,
                "ai2_sma_20": 40.0,
                "ai2_sma_50": 38.0,
                "ai2_rsi_14": 55,
                "ai2_atr_14": 1.1,
                "ai2_news_count": 2,
                "ai2_sentiment_score": 0.3,
                "ai2_news_attention_score": 0.4,
                "ai2_exchange": "NASDAQ",
                "ai2_currency": "USD",
                "ai2_security_type": "Common Stock",
            },
            {
                "symbol": "RISKY",
                "ai2_realtime_price": 12.0,
                "ai2_quote_timestamp": "2026-08-13T11:00:00Z",
                "ai2_quote_age_seconds": 1800,
                "ai2_sma_20": 11.5,
                "ai2_sma_50": 10.5,
                "ai2_rsi_14": 82,
                "ai2_atr_14": 1.8,
                "ai2_news_count": 4,
                "ai2_sentiment_score": -0.4,
                "ai2_news_attention_score": 0.9,
                "ai2_exchange": "NYSE",
                "ai2_currency": "USD",
                "ai2_security_type": "Common Stock",
            },
        ]
    )

    out = enrich_with_ai2_extended_diagnostics(frame, max_quote_age_seconds=900)
    by_symbol = {row["symbol"]: row for row in out.to_dict("records")}

    assert by_symbol["CLEAN"]["ai2_quote_status"] == "quote_fresh"
    assert by_symbol["CLEAN"]["ai2_news_status"] == "positive_news_support"
    assert by_symbol["CLEAN"]["ai2_identity_status"] == "identity_available"
    assert by_symbol["RISKY"]["ai2_quote_status"] == "quote_stale"
    assert by_symbol["RISKY"]["ai2_technical_status"] == "technical_overbought_watch"
    assert by_symbol["RISKY"]["ai2_news_status"] == "negative_news_watch"
    assert by_symbol["RISKY"]["ai2_extended_enrichment_recommendation"] == "would_review_news"


def test_extended_enrichment_writes_summary_and_coverage(tmp_path):
    source = tmp_path / "candidate.csv"
    pd.DataFrame(
        [
            {
                "symbol": "GCT",
                "ai2_realtime_price": 46.5,
                "ai2_quote_timestamp": "2026-08-13T12:00:00Z",
                "ai2_quote_age_seconds": 60,
                "ai2_exchange": "NASDAQ",
                "ai2_currency": "USD",
                "ai2_security_type": "Common Stock",
            }
        ]
    ).to_csv(source, index=False)

    result = run_ai2_extended_enrichment_diagnostics(candidate_file=source, output_dir=tmp_path, stamp="20260813_120001")

    assert result.group_coverage["realtime_quote"] == 1
    assert result.group_coverage["exchange_identity"] == 1
    assert result.group_coverage["technical"] == 0
    assert result.detail_path.exists()
    assert result.summary_path.exists()
    assert "Trading behavior changed: `false`" in result.summary_path.read_text(encoding="utf-8")
