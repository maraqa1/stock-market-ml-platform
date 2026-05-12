from __future__ import annotations

from typing import Any, Callable

from stockml.intraday import kill_switch
from stockml.trading.manual_position_actions import apply_manual_position_action


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
