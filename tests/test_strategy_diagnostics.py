from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.diagnostics import execution_attribution
from stockml.diagnostics import fallback_attribution
from stockml.diagnostics import intraday_promotion_ablation
from stockml.diagnostics import long_short_edge
from stockml.diagnostics import meta_label_impact
from stockml.diagnostics import position_management_attribution
from stockml.diagnostics import score_bucket_edge


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-06-01",
                "ticker": "AAA",
                "trade_action": "Long",
                "model_score": 0.90,
                "rank_overall": 1,
                "meta_label_decision": "Take Trade",
                "directional_action": "Long",
                "directional_strength": 0.99,
                "signal_reason": "rank_validation_gate_passed",
            },
            {
                "date": "2026-06-01",
                "ticker": "BBB",
                "trade_action": "Short",
                "model_score": 0.10,
                "rank_overall": 2,
                "meta_label_decision": "Skip",
                "directional_action": "Long",
                "directional_strength": 0.98,
                "signal_reason": "near_miss_fallback",
            },
        ]
    )


def _gold() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": "2026-06-01",
                "ticker": "AAA",
                "sector": "Technology",
                "forward_5d_return": 0.02,
                "forward_5d_alpha_vs_spy": 0.01,
                "forward_5d_alpha_vs_sector": 0.015,
            },
            {
                "date": "2026-06-01",
                "ticker": "BBB",
                "sector": "Financials",
                "forward_5d_return": -0.03,
                "forward_5d_alpha_vs_spy": -0.02,
                "forward_5d_alpha_vs_sector": -0.01,
            },
        ]
    )


def _write(path: Path, frame: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def test_strategy_diagnostic_reports_write_expected_outputs(tmp_path, monkeypatch):
    stamp = "20260605_120000"
    signal_file = _write(tmp_path / "signals.csv", _signals())
    gold_file = _write(tmp_path / "gold.csv", _gold())
    result_file = _write(
        tmp_path / "results.csv",
        pd.DataFrame(
            [
                {"symbol": "AAA", "side": "buy", "status": "submitted", "extended_hours": False},
                {"symbol": "BBB", "side": "sell", "status": "submitted", "extended_hours": True},
            ]
        ),
    )
    tracking_file = _write(
        tmp_path / "tracking.csv",
        pd.DataFrame(
            [
                {"symbol": "AAA", "side": "buy", "alpaca_status": "filled", "filled_qty": 1, "filled_avg_price": 10, "extended_hours": False},
                {"symbol": "BBB", "side": "sell", "alpaca_status": "new", "filled_qty": 0, "limit_price": 20, "extended_hours": True},
            ]
        ),
    )
    event_file = _write(
        tmp_path / "events.csv",
        pd.DataFrame(
            [
                {"position_id": "paper:AAA", "event_type": "monitor_close", "source": "autopilot", "details": "{'reason':'stop_loss'}"},
                {"position_id": "paper:BBB", "event_type": "operator_close", "source": "manual", "details": "{}"},
            ]
        ),
    )

    model_dir = tmp_path / "model_outputs"
    trading_dir = tmp_path / "trading"
    for module in [score_bucket_edge, long_short_edge, meta_label_impact, intraday_promotion_ablation]:
        monkeypatch.setattr(module, "MODEL_OUTPUTS_DIR", model_dir)
    for module in [execution_attribution, position_management_attribution, fallback_attribution]:
        monkeypatch.setattr(module, "TRADING_DIR", trading_dir)

    outputs = [
        score_bucket_edge.build_score_bucket_edge_report(stamp, signal_file=signal_file, gold_file=gold_file),
        long_short_edge.build_long_short_edge_report(stamp, signal_file=signal_file, gold_file=gold_file),
        meta_label_impact.build_meta_label_impact_report(stamp, signal_file=signal_file, gold_file=gold_file),
        intraday_promotion_ablation.build_intraday_promotion_ablation_report(stamp, signal_file=signal_file, gold_file=gold_file),
        execution_attribution.build_execution_attribution_report(stamp, result_file=result_file, tracking_file=tracking_file),
        position_management_attribution.build_position_management_report(stamp, event_file=event_file),
        fallback_attribution.build_fallback_attribution_report(stamp, signal_file=signal_file, gold_file=gold_file),
    ]

    assert all(output.path.exists() for output in outputs)
    assert all(output.status == "ok" for output in outputs)
    score = pd.read_csv(model_dir / f"diagnostics_score_bucket_edge_{stamp}.csv")
    assert {"diagnostic_side", "score_bucket", "mean_gain_after_cost_bps"}.issubset(score.columns)
    execution = pd.read_csv(trading_dir / f"diagnostics_execution_attribution_{stamp}.csv")
    assert set(execution["session"]) == {"regular", "extended_hours"}


def test_strategy_diagnostics_write_missing_data_rows(tmp_path, monkeypatch):
    stamp = "20260605_120001"
    monkeypatch.setattr(score_bucket_edge, "MODEL_OUTPUTS_DIR", tmp_path)

    output = score_bucket_edge.build_score_bucket_edge_report(stamp, signal_file=tmp_path / "missing.csv", gold_file=tmp_path / "missing_gold.csv")

    frame = pd.read_csv(output.path)
    assert output.status == "missing_data"
    assert frame.iloc[0]["status"] == "missing_data"
    assert "walk_forward_predictions_or_signal_table" in frame.iloc[0]["missing_inputs"]


def test_strategy_diagnostics_accept_legacy_gold_target_columns(tmp_path, monkeypatch):
    stamp = "20260605_120002"
    signal_file = _write(tmp_path / "signals.csv", _signals())
    gold_file = _write(
        tmp_path / "gold.csv",
        pd.DataFrame(
            [
                {"date": "2026-06-01", "ticker": "AAA", "sector": "Technology", "target_return_5d": 0.02, "target_sector_relative_return_5d": 0.01},
                {"date": "2026-06-01", "ticker": "BBB", "sector": "Financials", "target_return_5d": -0.03, "target_sector_relative_return_5d": -0.02},
            ]
        ),
    )
    monkeypatch.setattr(score_bucket_edge, "MODEL_OUTPUTS_DIR", tmp_path)

    output = score_bucket_edge.build_score_bucket_edge_report(stamp, signal_file=signal_file, gold_file=gold_file)

    frame = pd.read_csv(output.path)
    assert output.status == "ok"
    assert frame["observed_rows"].sum() == 2


def test_strategy_diagnostics_use_signal_embedded_target_columns(tmp_path, monkeypatch):
    stamp = "20260605_120003"
    signals = _signals()
    signals["target_return_5d"] = [0.02, -0.03]
    signals["target_sector_relative_return_5d"] = [0.01, -0.02]
    signal_file = _write(tmp_path / "walk_forward_predictions.csv", signals)
    monkeypatch.setattr(score_bucket_edge, "MODEL_OUTPUTS_DIR", tmp_path)

    output = score_bucket_edge.build_score_bucket_edge_report(stamp, signal_file=signal_file, gold_file=tmp_path / "missing_gold.csv")

    frame = pd.read_csv(output.path)
    assert output.status == "ok"
    assert frame["observed_rows"].sum() == 2
