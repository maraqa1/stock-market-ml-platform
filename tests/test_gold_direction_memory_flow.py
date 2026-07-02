import pandas as pd

from stockml.models.build_model_outputs import _enrich_artifact_direction_memory
from stockml.models.gold_direction_memory import enrich_gold_direction_memory_fields
from stockml.models.ranking_model import ModelArtifacts
from stockml.trading.order_builder import order_row
from stockml.trading.config import AlpacaConfig


def _frame():
    return pd.DataFrame(
        [
            {
                "date": "2026-07-01",
                "ticker": "AAA",
                "symbol": "AAA",
                "trade_action": "Long",
                "directional_action": "Long",
                "ticker_direction_sample_count": 42,
                "ticker_avg_long_alpha_bps_5d": 25.5,
                "ticker_avg_short_alpha_bps_5d": -25.5,
                "ticker_long_win_rate_5d": 0.61,
                "ticker_short_win_rate_5d": 0.39,
                "ticker_direction_memory_status": "available",
                "ticker_direction_bias_gold": "trust_long",
                "ticker_direction_reason_gold": "historical_ticker_long_alpha_positive",
                "trade_quality_status": "approved",
                "approved_notional": 100.0,
                "suggested_quantity": 1,
            },
            {
                "date": "2026-07-01",
                "ticker": "BBB",
                "symbol": "BBB",
                "trade_action": "No Decision",
                "directional_action": "No Decision",
                "ticker_direction_sample_count": 3,
                "ticker_direction_bias_gold": "insufficient_data",
                "ticker_direction_reason_gold": "insufficient_ticker_samples",
            },
        ]
    )


def _artifacts(frame):
    return ModelArtifacts(
        predictions=frame.copy(),
        signal_table=frame.copy(),
        top_long=frame.head(1).copy(),
        top_short=pd.DataFrame(columns=frame.columns),
        validation_leaderboard=pd.DataFrame(),
        bucket_performance=pd.DataFrame(),
        feature_importance=pd.DataFrame(),
        model_status=pd.DataFrame(),
        data_dictionary=pd.DataFrame(),
        walk_forward_predictions=pd.DataFrame(),
        fold_metrics=pd.DataFrame(),
        feature_audit=pd.DataFrame(),
        rejected_features=pd.DataFrame(),
        model_config={},
    )


def test_gold_direction_memory_fields_are_added_to_model_rows():
    enriched = enrich_gold_direction_memory_fields(_frame())

    first = enriched.iloc[0]
    assert first["ticker_direction_bias"] == "trust_long"
    assert first["ticker_direction_reason"] == "historical_ticker_long_alpha_positive"
    assert first["ticker_direction_memory_status"] == "available"
    assert first["ticker_direction_sample_count"] == 42
    assert first["ticker_direction_confidence"] == 0.61
    assert first["validated_expected_return_bps"] == 25.5
    assert first["validated_hit_rate"] == 0.61
    assert first["expected_return_scope"] == "ticker"
    assert first["hit_rate_scope"] == "ticker"


def test_model_artifacts_preserve_gold_direction_memory_outputs():
    artifacts = _enrich_artifact_direction_memory(_artifacts(_frame()))

    for frame in [artifacts.predictions, artifacts.signal_table, artifacts.top_long]:
        assert "ticker_direction_bias" in frame.columns
        assert "ticker_direction_memory_status" in frame.columns
        assert "expected_return_scope" in frame.columns
        assert frame.iloc[0]["ticker_direction_bias"] == "trust_long"


def test_order_rows_keep_gold_direction_memory_for_candidate_pool():
    enriched = enrich_gold_direction_memory_fields(_frame()).iloc[0]

    row = order_row(enriched, AlpacaConfig())

    assert row["ticker_direction_bias"] == "trust_long"
    assert row["ticker_direction_sample_count"] == 42
    assert row["ticker_direction_memory_status"] == "available"
    assert row["expected_return_scope"] == "ticker"

