from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, insert, select

from stockml.db.schema import create_all, intraday_features, same_day_candidates, same_day_signal_log
from stockml.same_day import gates
from stockml.same_day.score_worker import score_tick
from stockml.same_day.scoring import ConstantProbabilityModel, SameDayModelBundle, score_features
from stockml.same_day.training import evaluate_model_promotion, write_model_lineage


NOW = datetime(2026, 5, 12, 15, 0, tzinfo=timezone.utc)


def _engine():
    db = create_engine("sqlite:///:memory:", future=True)
    create_all(db)
    return db


def _features(**overrides):
    values = {
        "avg_dollar_volume_20d": 30_000_000,
        "last_price": 25,
        "market_cap": 800_000_000,
        "spread_bps": 5,
        "spread_bps_zscore_20d": 0.5,
        "is_first_15_min": False,
        "is_last_30_min": False,
        "seconds_since_signal_first_fired": 600,
        "is_halted": False,
        "earnings_today": False,
        "earnings_yesterday": False,
        "seconds_to_open": 9000,
        "market_aligned": True,
        "sector_aligned": True,
        "sector_etf_intraday_move_pct": 0.1,
        "borrow_available": True,
        "return_5m_pct": 0.01,
    }
    values.update(overrides)
    return values


def _insert_feature(db, symbol: str, features: dict | None = None, status: str = "ok"):
    with db.begin() as conn:
        result = conn.execute(
            insert(intraday_features).values(
                computed_at=NOW,
                decision_time=NOW,
                bar_close_at=NOW - timedelta(minutes=5),
                symbol=symbol,
                status=status,
                features=features or _features(),
            )
        )
        return result.inserted_primary_key[0]


def _bundle(long_p=0.72, short_p=0.20):
    return SameDayModelBundle(
        model_id="pytest-model",
        long_model=ConstantProbabilityModel(long_p),
        short_model=ConstantProbabilityModel(short_p),
        feature_list=["return_5m_pct"],
    )


def test_score_worker_writes_signal_log_for_every_universe_symbol():
    db = _engine()
    _insert_feature(db, "AAA")
    _insert_feature(db, "BBB")

    result = score_tick(decision_time=NOW, engine=db, model_loader=lambda: _bundle(), now=NOW)

    with db.connect() as conn:
        rows = conn.execute(select(same_day_signal_log.c.symbol)).all()
    assert result["signals_logged"] == 2
    assert [row[0] for row in rows] == ["AAA", "BBB"]


def test_score_worker_uses_latest_completed_feature_bucket():
    db = _engine()
    prior_decision = NOW - timedelta(minutes=5)
    with db.begin() as conn:
        conn.execute(
            insert(intraday_features).values(
                computed_at=NOW,
                decision_time=prior_decision,
                bar_close_at=prior_decision - timedelta(minutes=5),
                symbol="AAA",
                status="ok",
                features=_features(),
            )
        )

    result = score_tick(decision_time=NOW, engine=db, model_loader=lambda: _bundle(), now=NOW)

    with db.connect() as conn:
        rows = conn.execute(select(same_day_signal_log.c.symbol, same_day_signal_log.c.decision_time)).all()
    assert result["features_seen"] == 1
    assert result["decision_time"] == prior_decision
    assert rows[0][0] == "AAA"
    assert rows[0][1].replace(tzinfo=timezone.utc) == prior_decision


def test_score_worker_does_not_duplicate_signal_logs_for_same_bucket():
    db = _engine()
    _insert_feature(db, "AAA")

    first = score_tick(decision_time=NOW, engine=db, model_loader=lambda: _bundle(), now=NOW)
    second = score_tick(decision_time=NOW, engine=db, model_loader=lambda: _bundle(), now=NOW)

    with db.connect() as conn:
        rows = conn.execute(select(same_day_signal_log.c.symbol)).all()
    assert first["signals_logged"] == 1
    assert second["signals_logged"] == 0
    assert rows == [("AAA",)]


def test_candidates_emitted_only_when_gates_pass():
    db = _engine()
    _insert_feature(db, "PASS")
    _insert_feature(db, "FAIL", _features(spread_bps=99))

    result = score_tick(decision_time=NOW, engine=db, model_loader=lambda: _bundle(), now=NOW)

    with db.connect() as conn:
        candidates = conn.execute(select(same_day_candidates.c.symbol)).all()
        signals = conn.execute(select(same_day_signal_log.c.symbol, same_day_signal_log.c.block_reason)).all()

    assert result["signals_logged"] == 2
    assert result["candidates_emitted"] == 1
    assert candidates == [("PASS",)]
    assert ("FAIL", "REJECTED_WIDE_SPREAD") in signals


def test_model_promotion_blocked_on_calibration_failure():
    decision = evaluate_model_promotion(
        {"auc": 0.70, "top_bucket_calibration_error": 0.15, "max_feature_importance_share": 0.20, "mean_net_bps_at_060": 10},
        {"auc": 0.69, "mean_net_bps_at_060": 9},
    )

    assert decision.promoted is False
    assert "calibration_error_above_limit" in decision.reasons


def test_model_lineage_written_on_training(tmp_path: Path):
    path = write_model_lineage(
        tmp_path,
        model_id="model-1",
        direction="long",
        training_data_sha="abc123",
        hyperparameters={"n_estimators": 10},
        validation_metrics={"auc": 0.7},
        feature_list=["return_5m_pct"],
    )

    text = path.read_text(encoding="utf-8")
    assert '"model_id": "model-1"' in text
    assert '"training_data_sha": "abc123"' in text
    assert '"feature_list"' in text


def test_long_short_models_score_independently():
    long_score = score_features(_features(), _bundle(long_p=0.80, short_p=0.20))
    short_score = score_features(_features(), _bundle(long_p=0.10, short_p=0.75))

    assert long_score.direction == "long"
    assert long_score.continuation_probability == 0.80
    assert short_score.direction == "short"
    assert short_score.continuation_probability == 0.75


def test_score_worker_uses_gate_result_to_block_candidates():
    db = _engine()
    _insert_feature(db, "AAA")

    def block_gate(*args, **kwargs):
        return gates.GateResult(False, reason="REJECTED_DAILY_CANDIDATE_CAP", gate="daily")

    result = score_tick(decision_time=NOW, engine=db, model_loader=lambda: _bundle(), gate_evaluator=block_gate, now=NOW)

    with db.connect() as conn:
        candidates = conn.execute(select(same_day_candidates)).all()
        logs = conn.execute(select(same_day_signal_log.c.gate_outcome, same_day_signal_log.c.block_reason)).all()
    assert result["candidates_emitted"] == 0
    assert candidates == []
    assert logs == [("blocked:REJECTED_DAILY_CANDIDATE_CAP", "REJECTED_DAILY_CANDIDATE_CAP")]


def test_no_submit_order_calls_in_same_day_scoring_source():
    source_root = Path(__file__).resolve().parents[2] / "src" / "stockml" / "same_day"
    text = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))

    assert "submit_order" not in text
    assert "/v2/orders" not in text
