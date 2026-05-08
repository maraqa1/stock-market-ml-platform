from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.common.paths import PORTAL_OUTPUTS_DIR, ensure_data_dirs, timestamp
from stockml.trading.alpaca_client import AlpacaPaperClient
from stockml.trading.config import alpaca_config
from stockml.trading.order_planner import build_order_plan, latest_signal_table


def run_paper_trading(signal_file: Optional[Path] = None) -> dict[str, Path | int | bool]:
    ensure_data_dirs()
    config = alpaca_config()
    signals = latest_signal_table(signal_file)
    plan = build_order_plan(signals, config)
    stamp = timestamp()
    plan_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_plan_{stamp}.csv"
    result_path = PORTAL_OUTPUTS_DIR / f"08_alpaca_paper_order_results_{stamp}.csv"
    plan.to_csv(plan_path, index=False)

    result_rows = []
    if config.submit_orders and not plan.empty:
        client = AlpacaPaperClient(config)
        for order in plan.to_dict("records"):
            request = {key: order[key] for key in ["symbol", "notional", "side", "type", "time_in_force", "extended_hours", "client_order_id"]}
            try:
                response = client.submit_order(request)
                result_rows.append({"symbol": request["symbol"], "status": "submitted", "order_id": response.get("id"), "message": ""})
            except Exception as exc:
                result_rows.append({"symbol": request["symbol"], "status": "error", "order_id": "", "message": str(exc)})
    else:
        for order in plan.to_dict("records"):
            result_rows.append({"symbol": order["symbol"], "status": "dry_run", "order_id": "", "message": "STOCKML_ALPACA_SUBMIT_ORDERS is false"})

    pd.DataFrame(result_rows).to_csv(result_path, index=False)
    return {
        "orders_planned": len(plan),
        "orders_submitted": sum(1 for row in result_rows if row["status"] == "submitted"),
        "dry_run": not config.submit_orders,
        "plan_path": plan_path,
        "result_path": result_path,
    }

