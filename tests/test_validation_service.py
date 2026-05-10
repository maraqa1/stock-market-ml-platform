from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, insert

from portal.services.validation import (
    confidence_buckets,
    headline_metrics,
    record_training_results,
    top_features,
    validation_context,
    walk_forward_folds,
)
from stockml.db.schema import create_all, output_outcome, output_prediction


def _engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    create_all(engine)
    return engine


def _seed(engine):
    assert record_training_results(
        "model-v1",
        trained_at=datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc),
        oos_hit_pct=0.62,
        oos_excess_pct=0.018,
        promoted=True,
        notes="fixture",
        folds=[
            {"period": "2026-Q1", "train_rows": 1000, "test_rows": 200, "hit_pct": 0.6, "excess_pct": 0.01, "notes": "ok"},
            {"period": "2026-Q2", "train_rows": 1200, "test_rows": 220, "hit_pct": 0.64, "excess_pct": 0.02, "notes": "ok"},
        ],
        feature_importance=[
            {"feature_name": "rel_strength_spy_20d", "importance": 0.5},
            {"feature_name": "sma50_ratio", "importance": 0.25},
        ],
        target=engine,
    )
    with engine.begin() as conn:
        conn.execute(
            insert(output_prediction),
            [
                {"symbol": "AAA", "prediction_date": date(2026, 5, 1), "horizon_days": 5, "outperform_probability": 0.85, "expected_excess_return": 0.02, "confidence": 0.9, "model_version": "model-v1", "run_timestamp": datetime(2026, 5, 1, tzinfo=timezone.utc)},
                {"symbol": "BBB", "prediction_date": date(2026, 5, 2), "horizon_days": 5, "outperform_probability": 0.65, "expected_excess_return": 0.01, "confidence": 0.8, "model_version": "model-v1", "run_timestamp": datetime(2026, 5, 2, tzinfo=timezone.utc)},
                {"symbol": "CCC", "prediction_date": date(2026, 5, 3), "horizon_days": 5, "outperform_probability": 0.35, "expected_excess_return": -0.01, "confidence": 0.7, "model_version": "model-v1", "run_timestamp": datetime(2026, 5, 3, tzinfo=timezone.utc)},
            ],
        )
        conn.execute(
            insert(output_outcome),
            [
                {"symbol": "AAA", "prediction_date": date(2026, 5, 1), "evaluation_date": date(2026, 5, 6), "predicted_excess_return": 0.02, "actual_excess_return": 0.01, "outperformed": True, "model_version": "model-v1"},
                {"symbol": "BBB", "prediction_date": date(2026, 5, 2), "evaluation_date": date(2026, 5, 7), "predicted_excess_return": 0.01, "actual_excess_return": 0.03, "outperformed": True, "model_version": "model-v1"},
                {"symbol": "CCC", "prediction_date": date(2026, 5, 3), "evaluation_date": date(2026, 5, 8), "predicted_excess_return": -0.01, "actual_excess_return": -0.02, "outperformed": False, "model_version": "model-v1"},
            ],
        )


def test_validation_headline_metrics_include_calibration_and_sharpe():
    engine = _engine()
    _seed(engine)

    metrics = headline_metrics("model-v1", date(2026, 5, 1), date(2026, 5, 31), engine)

    assert metrics["hit_rate"] == pytest.approx(2 / 3)
    assert metrics["excess_ret"] == pytest.approx((0.01 + 0.03 - 0.02) / 3)
    assert metrics["calib_err"] == pytest.approx((0.01 + 0.02 + 0.01) / 3)
    assert metrics["sharpe"] is not None


def test_validation_sections_compose_from_fixture_data():
    engine = _engine()
    _seed(engine)

    assert [row["period"] for row in walk_forward_folds("model-v1", engine)] == ["2026-Q1", "2026-Q2"]
    buckets = confidence_buckets("model-v1", date(2026, 5, 1), date(2026, 5, 31), engine)
    assert sum(row["predictions"] for row in buckets) == 3
    features = top_features("model-v1", target=engine)
    assert features[0]["feature_name"] == "rel_strength_spy_20d"
    assert features[0]["bar_pct"] == pytest.approx(100.0)

    context = validation_context(model_version="model-v1", from_value="2026-05-01", to_value="2026-05-31", target=engine)
    assert context["model_version"] == "model-v1"
    assert len(context["leaderboard"]) == 1
    assert context["headline"]["prediction_count"] == 3
