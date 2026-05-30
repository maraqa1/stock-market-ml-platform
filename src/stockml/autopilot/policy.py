from __future__ import annotations

from typing import Any, Callable

from stockml.intraday import kill_switch
from stockml.trading.manual_position_actions import apply_manual_position_action
from stockml.trading.position_sizing import SameDaySizingConfig, load_same_day_sizing_config


def guarded_paper_close(
    symbol: str,
    *,
    source: str,
    action_func: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    verdict = kill_switch.gate(action="submit_order")
    if not verdict.allow:
        return {
            "status": "blocked",
            "message": "kill_switch_active",
            "symbol": str(symbol or "").upper(),
            "source": source,
            "tripped": verdict.tripped,
            "order_id": "",
        }
    close_func = action_func or apply_manual_position_action
    result = close_func(str(symbol or "").upper(), "close")
    result["source"] = source
    return result


def same_day_daily_loss_halt(
    realized_pnl_today: float,
    *,
    config: SameDaySizingConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_same_day_sizing_config()
    halted = float(realized_pnl_today or 0) <= cfg.max_loss_per_day_usd
    return {
        "halted": halted,
        "reason": "REJECTED_SAME_DAY_LOSS_LIMIT" if halted else "",
        "stream": "same_day_momentum",
        "multi_day_unaffected": True,
        "threshold": cfg.max_loss_per_day_usd,
    }
