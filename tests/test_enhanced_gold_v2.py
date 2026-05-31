from pathlib import Path

import pandas as pd

from stockml.gold.enhanced_gold_v2 import build_enhanced_gold_v2, build_feature_catalog, build_quality_report


def _gold_frame() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=8, freq="B")
    rows = []
    for ticker, offset in [("AAA", 0.05), ("BBB", -0.02), ("CCC", 0.0)]:
        for i, date in enumerate(dates):
            rows.append(
                {
                    "date": date,
                    "ticker": ticker,
                    "company": ticker,
                    "exchange": "NASDAQ",
                    "sector": "Semiconductors",
                    "industry": "Semiconductors",
                    "close": 10 + i,
                    "adj_close": 10 + i,
                    "volume": 1000,
                    "avg_dollar_volume_20d": 1_000_000,
                    "return_5d": offset,
                    "return_20d": offset,
                    "rsi_14": 55,
                    "volatility_20d": 0.2,
                    "target_return_5d": offset,
                    "target_return_10d": offset,
                    "target_sector_relative_return_5d": offset,
                }
            )
    return pd.DataFrame(rows).sort_values(["date", "ticker"]).reset_index(drop=True)


def test_enhanced_gold_v2_writes_separate_training_and_candidate_outputs(tmp_path: Path):
    source = tmp_path / "06_us_gold_ml_dataset_20240101_000000.csv"
    _gold_frame().to_csv(source, index=False)

    outputs = build_enhanced_gold_v2(source, stamp="20240102_000000", output_dir=tmp_path, candidate_limit=5, chunk_size=4)

    decision = pd.read_csv(outputs.decision_daily)
    candidates = pd.read_csv(outputs.candidates_latest)
    quality = pd.read_csv(outputs.data_quality_report)
    catalog = pd.read_csv(outputs.feature_catalog)
    assert len(decision) == len(_gold_frame())
    assert len(candidates) == 3
    assert candidates["date"].nunique() == 1
    assert outputs.decision_daily.name.startswith("gold_stock_decision_daily_")
    assert outputs.candidates_latest.name.startswith("gold_stock_candidates_latest_")
    assert "row_count" in set(quality["check"])
    assert "target_trade_label_5d" in set(catalog["column"])


def test_latest_rows_without_forward_data_have_na_labels(tmp_path: Path):
    frame = _gold_frame()
    latest = frame["date"].max()
    frame.loc[frame["date"].eq(latest), "target_sector_relative_return_5d"] = pd.NA
    source = tmp_path / "gold.csv"
    frame.to_csv(source, index=False)

    outputs = build_enhanced_gold_v2(source, stamp="20240102_000000", output_dir=tmp_path)
    decision = pd.read_csv(outputs.decision_daily)
    latest_rows = decision[pd.to_datetime(decision["date"]).eq(latest)]

    assert latest_rows["target_trade_label_5d"].isna().all()


def test_feature_catalog_marks_forward_and_target_columns_as_leakage():
    catalog = build_feature_catalog(["ticker", "return_5d", "forward_5d_return", "target_trade_label_5d"])
    leakage = catalog[catalog["is_target_or_leakage"]]

    assert set(leakage["column"]) == {"forward_5d_return", "target_trade_label_5d"}
    assert not catalog[catalog["column"].eq("forward_5d_return")].iloc[0]["allowed_model_input"]


def test_quality_report_fails_all_neutral_labels():
    frame = _gold_frame()
    frame["target_trade_label_5d"] = "Neutral"

    report = build_quality_report(frame)
    label_row = report[report["check"].eq("target_label_distribution")].iloc[0]

    assert label_row["status"] == "fail"


def test_score_columns_are_between_zero_and_one(tmp_path: Path):
    source = tmp_path / "gold.csv"
    _gold_frame().to_csv(source, index=False)

    outputs = build_enhanced_gold_v2(source, stamp="20240102_000000", output_dir=tmp_path)
    decision = pd.read_csv(outputs.decision_daily)

    for column in ["momentum_score", "relative_strength_score", "technical_entry_score", "final_trade_score", "trade_confidence"]:
        assert decision[column].between(0, 1).all()
