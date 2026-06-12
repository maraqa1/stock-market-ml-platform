from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.reports.ticker_lineage import build_ticker_lineage


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_ticker_lineage_joins_candidate_to_upstream_metadata_and_order(tmp_path: Path):
    _write(
        tmp_path / "data" / "raw" / "01_us_equity_universe_20260612_000000.csv",
        [{"symbol": "AAA", "source": "nasdaqlisted", "listing_exchange": "Q", "security_name": "AAA Inc", "financial_status": "N", "etf_flag": "N"}],
    )
    _write(
        tmp_path / "data" / "interim" / "02_us_universe_cleaned_20260612_000000.csv",
        [{"symbol": "AAA", "exclude_reason": ""}],
    )
    _write(
        tmp_path / "data" / "interim" / "02_us_tradable_universe_20260612_000000.csv",
        [{"symbol": "AAA", "exclude_reason": ""}],
    )
    _write(
        tmp_path / "data" / "interim" / "03_us_price_validated_universe_20260612_000000.csv",
        [{"symbol": "AAA", "ticker": "AAA", "price_quality_status": "ok", "min_date": "2025-01-01", "max_date": "2026-06-11", "latest_close": 10.5, "avg_dollar_volume_20d": 1_000_000}],
    )
    _write(
        tmp_path / "data" / "interim" / "04_us_metadata_enriched_20260612_000000.csv",
        [{"ticker": "AAA", "company": "AAA Inc", "exchange": "NASDAQ", "sector": "Technology", "industry": "Software", "market_cap": 100_000_000, "country": "United States", "currency": "USD", "metadata_status": "ok"}],
    )
    _write(
        tmp_path / "data" / "processed" / "05_us_feature_panel_20260612_000000.csv",
        [{"date": "2026-06-10", "ticker": "AAA", "return_5d": 0.01, "return_20d": 0.03, "rsi_14": 55, "volatility_20d": 0.02}],
    )
    _write(
        tmp_path / "data" / "processed" / "05_news_sentiment_store.csv",
        [{"date": "2026-06-10", "ticker": "AAA", "article_count": 3, "sentiment_score_mean": 0.2, "sentiment_status": "ok"}],
    )
    _write(
        tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260612_000000.csv",
        [{"date": "2026-06-10", "ticker": "AAA", "candidate_rank_overall": 4, "selection_score": 0.8, "target_trade_label_5d": "Trade_Buy"}],
    )
    _write(
        tmp_path / "data" / "model_outputs" / "advanced_model_signal_table_20260612_000000.csv",
        [{"date": "2026-06-10", "ticker": "AAA", "rank_overall": 2, "candidate_rank_overall": 4, "trade_action": "Long", "meta_label_decision": "Take Trade", "risk_adjusted_score": 1.2}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260612_000000.csv",
        [{"symbol": "AAA", "company": "AAA Inc", "sector": "Technology", "trade_action": "Long", "meta_label_decision": "Take Trade", "trade_quality_status": "approved", "candidate_status": "approved", "order_eligible": True, "risk_adjusted_score": 1.2}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_20260612_000000.csv",
        [{"symbol": "AAA", "side": "buy", "type": "limit", "extended_hours": True, "limit_price": 10.4, "approved_notional": 1000, "suggested_quantity": 96, "trade_quality_status": "approved", "client_order_id": "cid-1"}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_results_20260612_000000.csv",
        [{"symbol": "AAA", "status": "submitted", "alpaca_status": "pending_new"}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_tracking_20260612_000000.csv",
        [{"symbol": "AAA", "alpaca_status": "filled", "filled_qty": 96, "filled_avg_price": 10.4}],
    )
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_positions_20260612_000000.csv",
        [{"symbol": "AAA", "side": "long", "qty": 96, "market_value": 1005, "unrealized_pl": 5}],
    )

    result = build_ticker_lineage(root=tmp_path, stamp="20260612_010000")

    out = pd.read_csv(result["path"])
    row = out.iloc[0]
    assert result["rows"] == 1
    assert row["symbol"] == "AAA"
    assert row["raw_universe_seen"] is True or row["raw_universe_seen"] == True
    assert row["price_quality_status"] == "ok"
    assert row["company"] == "AAA Inc"
    assert row["model_meta_label_decision"] == "Take Trade"
    assert row["candidate_order_eligible"] is True or row["candidate_order_eligible"] == True
    assert row["order_plan_seen"] is True or row["order_plan_seen"] == True
    assert row["tracking_alpaca_status"] == "filled"
    assert row["position_seen"] is True or row["position_seen"] == True
    assert row["lineage_warnings"] == "" or pd.isna(row["lineage_warnings"])


def test_ticker_lineage_flags_candidate_without_model_row(tmp_path: Path):
    _write(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_candidate_pool_20260612_000000.csv",
        [{"symbol": "BBB", "company": "BBB Inc", "sector": "Industrials", "trade_action": "Long", "meta_label_decision": "Take Trade", "trade_quality_status": "approved", "candidate_status": "approved", "order_eligible": True, "risk_adjusted_score": 1.0}],
    )

    result = build_ticker_lineage(root=tmp_path, stamp="20260612_010000")

    out = pd.read_csv(result["path"])
    assert result["warnings"] == 1
    assert out.iloc[0]["symbol"] == "BBB"
    assert "candidate_without_model_row" in out.iloc[0]["lineage_warnings"]
