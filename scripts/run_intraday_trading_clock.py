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

    run_rotation_recommendations()

    allow_auto_open = refresh.get("status") == "ok"
    if not allow_auto_open:
        print("auto_open_gate:", f"skipped_{refresh.get('reason') or refresh.get('status')}")

    state = autopilot_tick(allow_auto_open=allow_auto_open)
    print("paper_autopilot_mode:", state.get("mode"))
    print("paper_autopilot_status:", state.get("status"))
    print("paper_autopilot_phase:", state.get("phase"))
    print("open_orders:", state.get("open_orders"))
    print("open_positions:", state.get("open_positions"))
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
