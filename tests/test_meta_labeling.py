from datetime import timedelta

import pandas as pd
import pytest

from stockml.decisions.meta_label_gate import MetaLabelGateConfig, evaluate_meta_label_gate
from stockml.models.meta_label_features import leakage_audit, selected_meta_features
from stockml.models.meta_label_targets import add_meta_label_targets, trade_examples
from stockml.models.meta_label_validation import walk_forward_meta_splits


def test_long_meta_label_target_is_positive_after_cost():
    frame = pd.DataFrame([{"ticker": "AAA", "trade_action": "Long", "target_return_5d": 0.02}])
    labeled = add_meta_label_targets(frame, transaction_cost_bps=10)

    assert labeled.iloc[0]["meta_realized_gain"] == 0.019
    assert labeled.iloc[0]["meta_label"] == 1


def test_short_meta_label_target_is_positive_after_cost():
    frame = pd.DataFrame([{"ticker": "BBB", "trade_action": "Short", "target_return_5d": -0.03}])
    labeled = add_meta_label_targets(frame, transaction_cost_bps=10)

    assert labeled.iloc[0]["meta_realized_gain"] == pytest.approx(0.029)
    assert labeled.iloc[0]["meta_label"] == 1


def test_no_decision_rows_are_not_trade_examples():
    frame = pd.DataFrame([{"ticker": "CCC", "trade_action": "No Decision", "target_return_5d": 0.10}])
    labeled = add_meta_label_targets(frame, transaction_cost_bps=10)

    assert labeled.iloc[0]["meta_label"] == 0
    assert trade_examples(labeled).empty


def test_leakage_columns_are_excluded_from_meta_features():
    frame = pd.DataFrame(
        [
            {
                "side_probability": 0.7,
                "target_return_5d": 0.02,
                "future_return": 0.03,
                "realized_gain": 0.01,
                "pnl_dollars": 5,
                "trade_action": "Long",
                "filled_avg_price": 12,
            }
        ]
    )
    assert selected_meta_features(frame) == ["side_probability"]
    audit = leakage_audit(frame).set_index("feature_name")
    assert bool(audit.loc["target_return_5d", "included"]) is False
    assert bool(audit.loc["trade_action", "included"]) is False
    assert bool(audit.loc["filled_avg_price", "included"]) is False


def test_low_meta_label_probability_blocks_trade():
    row = pd.Series(
        {
            "trade_action": "Long",
            "model_status": "decision_grade",
            "meta_label_probability": 0.59,
            "expected_trade_return": 0.02,
        }
    )
    passed, reason = evaluate_meta_label_gate(row, MetaLabelGateConfig(min_meta_label_probability=0.60))

    assert not passed
    assert reason == "meta_label_probability_below_threshold"


def test_high_meta_label_probability_allows_trade_only_if_risk_gate_passes():
    row = pd.Series(
        {
            "trade_action": "Short",
            "decision_grade": "decision_grade",
            "meta_label_probability": 0.80,
            "expected_trade_return": 0.02,
        }
    )

    assert evaluate_meta_label_gate(row, MetaLabelGateConfig(), risk_gate_passed=True) == (True, "meta_label_gate_passed")
    assert evaluate_meta_label_gate(row, MetaLabelGateConfig(), risk_gate_passed=False) == (False, "risk_gate_failed")


def test_embargoed_validation_prevents_overlap():
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    splits = walk_forward_meta_splits(dates, embargo_days=5, folds=3)

    assert splits
    for split in splits:
        assert split["train_end"] + timedelta(days=5) <= split["validation_start"]
