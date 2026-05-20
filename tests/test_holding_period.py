from pathlib import Path

import pandas as pd

from stockml.trading.holding_period import (
    build_holding_review,
    build_holding_period_report,
    classify_holding_quality,
    generate_holding_period_report,
    horizon_stats,
    load_gold_history_for_symbols,
)


def _history(symbol: str = "AAA") -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=30, freq="D")
    prices = [100 + i for i in range(30)]
    return pd.DataFrame({"date": dates, "ticker": symbol, "adj_close": prices, "close": prices})


def _plan(**overrides) -> pd.DataFrame:
    row = {
        "symbol": "AAA",
        "side": "buy",
        "trade_action": "Long",
        "trade_quality_status": "approved",
        "notional": 250.0,
        "suggested_quantity": 2,
        "current_price": 120.0,
        "risk_tier": "high_quality",
        "volatility_tier": "medium",
        "liquidity_tier": "high",
        "max_holding_days": 10,
        "stop_loss_price": 116.4,
        "take_profit_price": 127.2,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_horizon_stats_flip_returns_for_short_side():
    long_stats = {item.horizon_days: item for item in horizon_stats(_history(), "AAA", "buy")}
    short_stats = {item.horizon_days: item for item in horizon_stats(_history(), "AAA", "sell")}

    assert long_stats[1].median_bps > 0
    assert short_stats[1].median_bps < 0
    assert long_stats[1].hit_rate == 1.0
    assert short_stats[1].hit_rate == 0.0


def test_build_holding_period_report_recommends_positive_edge_window():
    report = build_holding_period_report(_plan(), _history())
    row = report.iloc[0]

    assert row["symbol"] == "AAA"
    assert row["recommended_holding_days"] in {1, 3, 5, 10}
    assert row["review_after_days"] >= 1
    assert row["max_holding_days"] <= 10
    assert row["median_directional_return_bps"] > 0
    assert row["exit_rule"] == "stop_or_take_profit_first;daily_review;close_at_max_holding_days"


def test_speculative_or_high_volatility_plan_uses_shorter_max_hold():
    report = build_holding_period_report(_plan(risk_tier="speculative", volatility_tier="high"), _history())

    assert report.iloc[0]["review_after_days"] == 1
    assert report.iloc[0]["max_holding_days"] <= max(3, report.iloc[0]["recommended_holding_days"])


def test_generate_holding_period_report_writes_artifact(tmp_path: Path):
    plan_path = tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_20260520_090000.csv"
    gold_path = tmp_path / "data" / "gold" / "06_us_gold_ml_dataset_20260520_060000.csv"
    plan_path.parent.mkdir(parents=True)
    gold_path.parent.mkdir(parents=True)
    _plan().to_csv(plan_path, index=False)
    _history().to_csv(gold_path, index=False)

    result = generate_holding_period_report(tmp_path, stamp="20260520_091500")

    output = Path(result["path"])
    written = pd.read_csv(output)
    assert result["rows"] == 1
    assert result["review_rows"] == 1
    assert Path(result["review_path"]).name == "holding_review_20260520_091500.csv"
    assert output.name == "holding_period_report_20260520_091500.csv"
    assert written.iloc[0]["symbol"] == "AAA"


def test_load_gold_history_for_symbols_reads_only_selected_tickers(tmp_path: Path):
    gold_path = tmp_path / "gold.csv"
    pd.concat([_history("AAA"), _history("BBB")], ignore_index=True).to_csv(gold_path, index=False)

    loaded = load_gold_history_for_symbols(gold_path, ["BBB"], chunksize=10)

    assert set(loaded["ticker"]) == {"BBB"}


def test_classify_holding_quality_marks_strong_watch_and_avoid():
    strong = pd.Series({"median_directional_return_bps": 75, "hit_rate": 0.56, "sample_count": 500})
    watch = pd.Series({"median_directional_return_bps": 10, "hit_rate": 0.53, "sample_count": 500})
    avoid = pd.Series({"median_directional_return_bps": -5, "hit_rate": 0.49, "sample_count": 500})

    assert classify_holding_quality(strong) == (
        "strong",
        "trade_normal_size",
        True,
        "positive_holding_edge_strong",
    )
    assert classify_holding_quality(watch) == (
        "watch",
        "trade_reduced_size_or_wait_for_intraday_confirmation",
        True,
        "positive_holding_edge_watch",
    )
    assert classify_holding_quality(avoid) == (
        "avoid",
        "skip_or_require_manual_override",
        False,
        "holding_edge_not_confirmed",
    )


def test_build_holding_review_adds_actionable_gate_fields():
    review = build_holding_review(_plan(), _history())

    assert {"holding_quality", "recommended_action", "holding_gate_pass", "holding_gate_reason"}.issubset(review.columns)
    assert review.iloc[0]["holding_quality"] in {"strong", "watch", "avoid"}
