from __future__ import annotations

import pandas as pd
import pytest

from scripts import measure_same_day_edge
from stockml.same_day.training import (
    balance_classes,
    build_markdown_report,
    classify_verdict,
    compute_minimal_features,
    split_holdout,
    walk_forward_folds,
)


def _feature_bars(decision: pd.Timestamp, decision_close: float = 500) -> pd.DataFrame:
    times = pd.date_range(decision - pd.Timedelta(minutes=60), periods=16, freq="5min", tz="UTC")
    rows = []
    price = 100.0
    for ts in times:
        rows.append(
            {
                "timestamp": ts,
                "symbol": "AAA",
                "open": price,
                "high": price + 0.5,
                "low": price - 0.5,
                "close": price + 0.1,
                "volume": 1000,
                "vwap": price,
            }
        )
        price += 0.2
    frame = pd.DataFrame(rows)
    frame.loc[frame["timestamp"].eq(decision), "close"] = decision_close
    return frame


def test_feature_lag_no_lookahead():
    decision = pd.Timestamp("2026-05-28T15:00:00Z")
    first = compute_minimal_features(_feature_bars(decision, 100), decision)
    second = compute_minimal_features(_feature_bars(decision, 1000), decision)

    assert first == second


def test_walk_forward_split_no_overlap():
    timestamps = pd.date_range("2026-01-05", periods=70, freq="D", tz="UTC")
    frame = pd.DataFrame({"timestamp": timestamps, "label": [0, 1] * 35})

    folds = walk_forward_folds(frame, folds=4)

    assert folds
    for fold in folds:
        assert fold.train_end < fold.test_start


def test_holdout_strictly_after_training():
    timestamps = pd.date_range("2026-01-01", periods=30, freq="D", tz="UTC")
    frame = pd.DataFrame({"timestamp": timestamps, "label": [0, 1] * 15})

    train, holdout = split_holdout(frame, holdout_days=5)

    assert train["timestamp"].max() < holdout["timestamp"].min()


def test_class_balance_recorded():
    frame = pd.DataFrame({"label": [0, 0, 0, 1], "timestamp": pd.date_range("2026-01-01", periods=4, tz="UTC")})

    balanced, info = balance_classes(frame)

    assert info["pre_downsample"] == {0: 3, 1: 1}
    assert info["post_downsample"] == {0: 1, 1: 1}
    assert len(balanced) == 2


def test_report_verdict_thresholds():
    green = [{"threshold": 0.55, "mean_net_bps": 20, "t_stat": 1}, {"threshold": 0.60, "mean_net_bps": 18, "t_stat": 2.1}, {"threshold": 0.65, "mean_net_bps": 16, "t_stat": 1}]
    amber = [{"threshold": 0.55, "mean_net_bps": 8, "t_stat": 0.5}, {"threshold": 0.60, "mean_net_bps": 5, "t_stat": 1.5}, {"threshold": 0.65, "mean_net_bps": -1, "t_stat": 0.5}]
    red = [{"threshold": 0.55, "mean_net_bps": -2, "t_stat": 0.5}, {"threshold": 0.60, "mean_net_bps": -1, "t_stat": 0.5}, {"threshold": 0.65, "mean_net_bps": -3, "t_stat": 0.5}]

    assert classify_verdict(green) == "GREEN"
    assert classify_verdict(amber) == "AMBER"
    assert classify_verdict(red) == "RED"


def test_universe_survivorship():
    frame = pd.DataFrame(
        {
            "symbol": ["LIVE", "DEAD", "DEAD"],
            "timestamp": pd.to_datetime(["2026-01-05", "2026-01-05", "2026-01-20"], utc=True),
            "delisted_at": pd.to_datetime([None, "2026-01-15", "2026-01-15"], utc=True),
        }
    )

    eligible = frame[(frame["delisted_at"].isna()) | (frame["timestamp"] < frame["delisted_at"])]

    assert set(eligible["symbol"]) == {"LIVE", "DEAD"}
    assert len(eligible[eligible["symbol"].eq("DEAD")]) == 1


def test_report_records_class_balances():
    samples = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "direction": ["long", "short"],
            "label": [1, 0],
            "time_of_day_bucket": [1, 1],
            "liquidity_tier": ["high", "high"],
        }
    )
    holdout = samples.assign(predicted_probability=[0.7, 0.2], realized_move_bps=[60, 10], net_bps=[50, 0])

    report = build_markdown_report(samples, holdout, {"pre_downsample": {0: 10, 1: 5}, "post_downsample": {0: 5, 1: 5}})

    assert "Class balance before downsampling: {0: 10, 1: 5}" in report
    assert "Class balance after downsampling: {0: 5, 1: 5}" in report


def test_measure_same_day_edge_missing_file_message(capsys):
    with pytest.raises(SystemExit):
        measure_same_day_edge.main(["--bars-file", "missing_5min_bars.csv"])

    err = capsys.readouterr().err
    assert "bars file not found" in err
    assert "scripts/download_intraday_history.py" in err
