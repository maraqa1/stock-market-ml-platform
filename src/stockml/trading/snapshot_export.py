from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from portal.services.near_miss_service import near_miss_context
from portal.services.per_symbol_forecast_service import per_symbol_forecast_context
from portal.services.trading_api_service import action_queue_context, intraday_promotion_context, positions_context
from portal.services.trading_service import trading_context
from stockml.trading.snapshot_writer import write_snapshot_csv, write_snapshot_file


SNAPSHOT_DIR = Path("data") / "trading" / "snapshots"


def snapshot_pools(root: Path) -> list[tuple[str, list[dict[str, Any]], Any, str]]:
    trading = trading_context(root)
    positions = positions_context(root)
    queue = action_queue_context(root)
    promotions = intraday_promotion_context(root)
    near_miss = near_miss_context(root)
    per_symbol_forecast = per_symbol_forecast_context(root, limit=1000)
    return [
        ("model_shortlist", trading.get("candidate_pool_rows", []), "", "candidate_pool_artifact"),
        ("todays_basket", trading.get("basket_rows", []), "", "order_plan_and_results"),
        ("rejected_trimmed", trading.get("rejected_trimmed_rows", []), "", "guardrails_and_broker_results"),
        ("open_positions", positions.get("positions", []), positions.get("refreshed_at", ""), "broker_positions"),
        ("action_queue", queue.get("items", []), queue.get("generated_at", ""), "monitor_and_operator_queue"),
        ("intraday_promotion", promotions.get("rows", []), promotions.get("latest_tick", ""), "promotion_log"),
        ("near_miss", near_miss.get("rows", []), near_miss.get("file_name", ""), "near_miss_analysis"),
        ("per_symbol_forecast", per_symbol_forecast.get("rows", []), per_symbol_forecast.get("file_name", ""), "per_symbol_forecast"),
    ]


def snapshot_csv_payload(root: Path, *, snapshot_at: datetime | None = None) -> str:
    return write_snapshot_csv(snapshot_pools(root), snapshot_at=snapshot_at or datetime.now(timezone.utc))


def export_trading_snapshot(root: Path, *, stamp: str | None = None, snapshot_at: datetime | None = None) -> dict[str, Any]:
    snapshot_time = snapshot_at or datetime.now(timezone.utc)
    run_stamp = stamp or snapshot_time.strftime("%Y%m%d_%H%M%S")
    path = root / SNAPSHOT_DIR / f"trading_snapshot_{run_stamp}.csv"
    write_snapshot_file(path, snapshot_pools(root), snapshot_at=snapshot_time)
    return {"status": "ok", "path": str(path), "rows": max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1)}
