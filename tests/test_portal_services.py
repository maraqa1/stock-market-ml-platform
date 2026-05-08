from pathlib import Path
import time

import pandas as pd

from portal.services.latest_file_reader import latest_file, readable_reason
from portal.services.universe_service import universe_context
from portal.services.signal_service import signal_context
from portal.services.trading_service import trading_context


def write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_latest_file_selection(tmp_path):
    older = tmp_path / "data" / "raw" / "01_us_equity_universe_old.csv"
    newer = tmp_path / "data" / "raw" / "01_us_equity_universe_new.csv"
    write_csv(older, [{"ticker": "AAA"}])
    time.sleep(0.01)
    write_csv(newer, [{"ticker": "BBB"}])
    assert latest_file(tmp_path, "raw", "01_us_equity_universe_*.csv") == newer


def test_missing_file_behavior(tmp_path):
    ctx = universe_context(tmp_path)
    assert ctx["raw_count"] == 0
    assert ctx["tradable_count"] == 0
    assert ctx["files"][0]["exists"] is False


def test_reason_formatter():
    assert readable_reason("weak_probability") == "Probability below decision threshold"
    assert readable_reason("not_in_top_ranked_long_or_short_candidates") == "Not ranked strongly enough today"


def test_signal_context_with_fixture(tmp_path):
    write_csv(
        tmp_path / "data" / "model_outputs" / "advanced_model_signal_table_1.csv",
        [
            {"ticker": "AAA", "trade_action": "Long", "signal_reason": "strong_probability"},
            {"ticker": "BBB", "trade_action": "No Decision", "no_decision_reason": "weak_probability"},
        ],
    )
    write_csv(
        tmp_path / "data" / "model_outputs" / "advanced_model_model_status_1.csv",
        [{"decision_grade": "decision_grade", "selected_model": "LightGBM", "reason": "ok"}],
    )
    ctx = signal_context(tmp_path)
    assert ctx["long_count"] == 1
    assert ctx["no_decision_count"] == 1


def test_trading_context_with_alpaca_artifacts(tmp_path):
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_plan_1.csv",
        [{"symbol": "AAA", "side": "buy", "notional": 500, "trade_action": "Long", "side_probability": 0.7}],
    )
    write_csv(
        tmp_path / "data" / "portal_outputs" / "08_alpaca_paper_order_results_1.csv",
        [{"symbol": "AAA", "status": "dry_run", "order_id": "", "message": "disabled"}],
    )
    ctx = trading_context(tmp_path)
    assert ctx["orders_planned"] == 1
    assert ctx["orders_submitted"] == 0
    assert ctx["dry_run"] is True
    assert ctx["total_notional"] == 500
