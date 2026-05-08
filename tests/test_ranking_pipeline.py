import pandas as pd

from stockml.models.ranking_model import (
    RankingConfig,
    construct_ranking_targets,
    feature_audit,
    walk_forward_splits,
    _group_sizes,
    _decision_gates,
)


def test_construct_ranking_targets_sorts_and_builds_quintiles():
    frame = pd.DataFrame(
        [
            {"date": "2024-01-03", "ticker": "BBB", "sector": "Tech", "adj_close": 20},
            {"date": "2024-01-01", "ticker": "AAA", "sector": "Tech", "adj_close": 10},
            {"date": "2024-01-08", "ticker": "BBB", "sector": "Tech", "adj_close": 30},
            {"date": "2024-01-08", "ticker": "AAA", "sector": "Tech", "adj_close": 20},
            {"date": "2024-01-01", "ticker": "BBB", "sector": "Tech", "adj_close": 20},
            {"date": "2024-01-03", "ticker": "AAA", "sector": "Tech", "adj_close": 10},
        ]
    )
    out = construct_ranking_targets(frame)
    assert list(out[["ticker", "date"]].head(2)["ticker"]) == ["AAA", "AAA"]
    assert "target_rank_pct_5d" in out.columns
    assert set(out["target_relevance_5d"].dropna().unique()).issubset({0, 1, 2})


def test_feature_audit_excludes_leakage_and_composite_scores():
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=3),
            "ticker": ["AAA", "BBB", "CCC"],
            "return_20d": [0.1, 0.2, 0.3],
            "target_return_5d": [0.2, 0.1, -0.1],
            "future_return": [1, 2, 3],
            "selection_score": [0.5, 0.6, 0.7],
            "candidate_rank_overall": [1, 2, 3],
        }
    )
    features, audit, rejected = feature_audit(frame)
    assert "return_20d" in features
    assert "target_return_5d" not in features
    assert "future_return" not in features
    assert "selection_score" not in features
    assert "candidate_rank_overall" not in features
    assert set(["feature_name", "included", "exclusion_reason", "leakage_risk_level"]).issubset(audit.columns)
    assert "target_return_5d" in set(rejected["feature_name"])


def test_walk_forward_splits_are_time_ordered_and_non_overlapping():
    dates = pd.date_range("2020-01-01", periods=800, freq="B")
    folds = walk_forward_splits(dates, RankingConfig(min_train_dates=100, validation_dates=50, folds=4))
    assert len(folds) == 4
    for fold in folds:
        assert fold["train_end"] < fold["validation_start"]
        assert fold["validation_start"] <= fold["validation_end"]


def test_group_sizes_match_rows_per_date():
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-01", "2024-01-02"],
            "ticker": ["AAA", "BBB", "AAA"],
        }
    )
    assert _group_sizes(frame) == [2, 1]


def test_top_k_portfolio_gate_can_pass_with_negative_full_rank_ic():
    leaderboard = pd.DataFrame(
        [
            {
                "model_name": "lightgbm_lambdarank",
                "daily_spearman_ic_mean": -0.01,
                "icir_5d": -0.05,
                "long_short_spread": 0.02,
                "tc_adjusted_long_short_spread": 0.019,
                "long_short_spread_ir": 0.2,
                "max_drawdown": 0.4,
                "turnover": 0.2,
            },
            {
                "model_name": "lightgbm_lambdarank",
                "daily_spearman_ic_mean": -0.02,
                "icir_5d": -0.08,
                "long_short_spread": 0.01,
                "tc_adjusted_long_short_spread": 0.009,
                "long_short_spread_ir": 0.1,
                "max_drawdown": 0.4,
                "turnover": 0.2,
            },
            {
                "model_name": "lightgbm_lambdarank",
                "daily_spearman_ic_mean": 0.01,
                "icir_5d": 0.04,
                "long_short_spread": 0.03,
                "tc_adjusted_long_short_spread": 0.029,
                "long_short_spread_ir": 0.3,
                "max_drawdown": 0.4,
                "turnover": 0.2,
            },
            {
                "model_name": "return_20d",
                "long_short_spread": 0.0,
            },
            {
                "model_name": "return_60d",
                "long_short_spread": -0.01,
            },
        ]
    )
    passed, reason = _decision_gates(leaderboard, RankingConfig(min_positive_spread_folds=3))
    assert passed is True
    assert "portfolio_validation_gates_passed" in reason
    assert "full_rank_ic_not_positive" in reason


def test_full_rank_ic_gate_still_blocks_negative_ic_when_required():
    leaderboard = pd.DataFrame(
        [
            {
                "model_name": "lightgbm_lambdarank",
                "daily_spearman_ic_mean": -0.01,
                "icir_5d": -0.05,
                "long_short_spread": 0.02,
                "tc_adjusted_long_short_spread": 0.019,
                "long_short_spread_ir": 0.2,
                "max_drawdown": 0.4,
                "turnover": 0.2,
            },
            {
                "model_name": "return_20d",
                "long_short_spread": 0.0,
            },
        ]
    )
    passed, reason = _decision_gates(
        leaderboard,
        RankingConfig(decision_objective="full_rank_ic", require_positive_full_ic=True, min_positive_spread_folds=1),
    )
    assert passed is False
    assert "mean_ic_not_positive" in reason
