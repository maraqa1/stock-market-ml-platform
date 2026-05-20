#!/opt/jupyter-env/bin/python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stockml.intraday.refresh import candidate_refresh_tick, prune_old_snapshots
from stockml.intraday.promotion_score import score_unscored_snapshots
from stockml.trading.per_symbol_forecast import generate_per_symbol_forecast
from stockml.trading.holding_period import generate_holding_period_report
from stockml.trading.paper_autopilot import load_state as load_autopilot_state
from stockml.trading.paper_autopilot import start as start_autopilot
from stockml.trading.paper_autopilot import tick as autopilot_tick
from stockml.trading.snapshot_export import export_trading_snapshot

from scripts.run_rotation_recommendations import main as run_rotation_recommendations


def main() -> int:
    refresh = candidate_refresh_tick()
    print("candidate_refresh_status:", refresh.get("status"))
    print("candidate_refresh_reason:", refresh.get("reason", ""))
    print("symbols:", len(refresh.get("symbols", [])))
    print("snapshots_written:", refresh.get("snapshots_written", 0))
    print("snapshots_pruned:", prune_old_snapshots())

    scoring = score_unscored_snapshots()
    print("intraday_promotion_status:", scoring.get("status"))
    print("snapshots_scored:", scoring.get("snapshots_scored", 0))
    print("verdict_counts:", scoring.get("verdict_counts", {}))

    try:
        forecast = generate_per_symbol_forecast(ROOT)
    except Exception as exc:
        forecast = {"status": "error", "reason": str(exc), "rows": 0, "path": ""}
    print("per_symbol_forecast_status:", forecast.get("status"))
    print("per_symbol_forecast_rows:", forecast.get("rows", 0))
    print("per_symbol_forecast_path:", forecast.get("path", ""))

    try:
        holding = generate_holding_period_report(ROOT)
    except Exception as exc:
        holding = {"status": "error", "reason": str(exc), "rows": 0, "path": ""}
    print("holding_period_status:", holding.get("status"))
    print("holding_period_rows:", holding.get("rows", 0))
    print("holding_period_path:", holding.get("path", ""))
    print("holding_review_path:", holding.get("review_path", ""))
    print("holding_review_passed:", holding.get("review_passed", 0))
    print("holding_review_blocked:", holding.get("review_blocked", 0))

    run_rotation_recommendations()

    allow_auto_open = refresh.get("status") == "ok"
    if not allow_auto_open:
        print("auto_open_gate:", f"skipped_{refresh.get('reason') or refresh.get('status')}")

    current_autopilot = load_autopilot_state()
    if current_autopilot.get("mode") == "paper_autopilot" and current_autopilot.get("status") != "running":
        last_error = current_autopilot.get("last_error")
        termination_reason = current_autopilot.get("termination_reason")
        benign_stop = termination_reason in {"", "no_open_orders_or_positions"} or (
            termination_reason == "autopilot_error" and last_error == "autopilot_not_running"
        )
        benign_error = last_error in {"", "autopilot_not_running"}
        if benign_stop and benign_error:
            restarted = start_autopilot()
            print("paper_autopilot_rearm:", restarted.get("status"))

    state = autopilot_tick(allow_auto_open=allow_auto_open)
    print("paper_autopilot_mode:", state.get("mode"))
    print("paper_autopilot_status:", state.get("status"))
    print("paper_autopilot_phase:", state.get("phase"))
    print("open_orders:", state.get("open_orders"))
    print("open_positions:", state.get("open_positions"))
    print("auto_rotations_attempted:", state.get("auto_rotations_attempted", 0))
    print("auto_rotations_confirmed:", state.get("auto_rotations_confirmed", 0))
    print("auto_rotation_notes:", state.get("auto_rotation_notes", ""))
    print("autopilot_open_submitted:", state.get("autopilot_open_submitted"))
    print("autopilot_open_notes:", state.get("autopilot_open_notes"))
    print("last_error:", state.get("last_error"))

    snapshot = export_trading_snapshot(ROOT)
    print("trading_snapshot_status:", snapshot.get("status"))
    print("trading_snapshot_rows:", snapshot.get("rows", 0))
    print("trading_snapshot_path:", snapshot.get("path", ""))
    return 0 if not state.get("last_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
