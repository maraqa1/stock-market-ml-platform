from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from stockml.intraday import kill_switch


@dataclass(frozen=True)
class SameDayAutoConfig:
    same_day_auto_enabled: bool = False
    min_continuation_probability: float = 0.65
    max_auto_opens_per_day: int = 5


def evaluate_auto_open_candidate(
    candidate: dict[str, Any],
    *,
    contract_met: bool,
    config: SameDayAutoConfig | None = None,
    todays_auto_opens: int = 0,
    gate_func: Callable[..., Any] = kill_switch.gate,
) -> dict[str, Any]:
    cfg = config or SameDayAutoConfig()
    if not contract_met or not cfg.same_day_auto_enabled:
        return {"action": "paper_assist", "allowed": False, "reason": "SAME_DAY_AUTO_DISABLED"}
    probability = float(candidate.get("continuation_probability") or 0)
    if probability < cfg.min_continuation_probability:
        return {"action": "paper_assist", "allowed": False, "reason": "REJECTED_CONTINUATION_THRESHOLD"}
    if todays_auto_opens >= cfg.max_auto_opens_per_day:
        return {"action": "paper_assist", "allowed": False, "reason": "REJECTED_DAILY_CANDIDATE_CAP"}
    verdict = gate_func(action="evaluate")
    if not getattr(verdict, "allow", False):
        return {"action": "paper_assist", "allowed": False, "reason": "BLOCKED_KILL_SWITCH"}
    return {"action": "auto_open", "allowed": True, "reason": "AUTO_OPEN_ALLOWED"}


def auto_open_candidates(
    candidates: list[dict[str, Any]],
    *,
    contract_met: bool,
    config: SameDayAutoConfig | None = None,
    paper_order_func: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    gate_func: Callable[..., Any] = kill_switch.gate,
) -> dict[str, Any]:
    cfg = config or SameDayAutoConfig()
    opened = 0
    notes = []
    for candidate in candidates:
        decision = evaluate_auto_open_candidate(candidate, contract_met=contract_met, config=cfg, todays_auto_opens=opened, gate_func=gate_func)
        if not decision["allowed"]:
            notes.append(f"{candidate.get('symbol', '')}:blocked:{decision['reason']}")
            continue
        if paper_order_func is None:
            notes.append(f"{candidate.get('symbol', '')}:blocked:paper_order_func_missing")
            continue
        result = paper_order_func({**candidate, "strategy_stream": "same_day_momentum", "must_flatten_at_eod": True})
        if str(result.get("status") or "").lower() in {"submitted", "opened", "ok"}:
            opened += 1
            notes.append(f"{candidate.get('symbol', '')}:opened:{result.get('order_id', '')}")
        else:
            notes.append(f"{candidate.get('symbol', '')}:blocked:{result.get('message', 'paper_order_rejected')}")
    return {"opened": opened, "notes": "; ".join(notes)}
