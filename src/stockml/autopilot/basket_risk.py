from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from stockml.common.paths import PROJECT_ROOT


@dataclass(frozen=True)
class BasketRiskConfig:
    pause_new_entries_if_red_position_pct_above: float = 0.70
    pause_new_entries_if_basket_return_below: float = -0.0075
    resume_new_entries_if_basket_return_above: float = -0.0025
    min_positions_for_percentage_rule: int = 5
    small_book_basket_return_floor_pct: float = -0.015
    small_book_loss_floor_pct: float = -0.02


@dataclass(frozen=True)
class BasketRiskState:
    basket_state: str
    red_position_pct: float
    basket_return: float
    new_entries_paused: bool
    reason: str = ""
    reason_text: str = ""
    open_position_count: int = 0
    daily_realized_loss_pct: float = 0.0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except Exception:
        return default


def load_basket_risk_config(root: Path | str | None = None) -> BasketRiskConfig:
    base = Path(root).resolve() if root else PROJECT_ROOT
    path = base / "config" / "autopilot.yaml"
    if not path.exists():
        return BasketRiskConfig()
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return BasketRiskConfig()
    section = payload.get("basket_risk") if isinstance(payload, dict) else {}
    section = section if isinstance(section, dict) else {}
    defaults = BasketRiskConfig()
    return BasketRiskConfig(
        pause_new_entries_if_red_position_pct_above=float(section.get("pause_new_entries_if_red_position_pct_above", defaults.pause_new_entries_if_red_position_pct_above)),
        pause_new_entries_if_basket_return_below=float(section.get("pause_new_entries_if_basket_return_below", defaults.pause_new_entries_if_basket_return_below)),
        resume_new_entries_if_basket_return_above=float(section.get("resume_new_entries_if_basket_return_above", defaults.resume_new_entries_if_basket_return_above)),
        min_positions_for_percentage_rule=int(section.get("min_positions_for_percentage_rule", defaults.min_positions_for_percentage_rule)),
        small_book_basket_return_floor_pct=float(section.get("small_book_basket_return_floor_pct", defaults.small_book_basket_return_floor_pct)),
        small_book_loss_floor_pct=float(section.get("small_book_loss_floor_pct", defaults.small_book_loss_floor_pct)),
    )


def evaluate_basket_risk(
    positions: Iterable[dict[str, Any]],
    *,
    config: BasketRiskConfig | None = None,
    previous_state: str | None = None,
    daily_realized_pnl: float = 0.0,
    account_equity: float = 0.0,
) -> BasketRiskState:
    cfg = config or BasketRiskConfig()
    rows = [dict(row) for row in positions]
    if not rows:
        return BasketRiskState("normal", 0.0, 0.0, False, "", "", 0, 0.0)

    red_count = sum(1 for row in rows if _float(row.get("unrealized_plpc")) < 0)
    red_pct = red_count / len(rows)
    cost_basis = sum(abs(_float(row.get("cost_basis"))) for row in rows)
    unrealized_pl = sum(_float(row.get("unrealized_pl")) for row in rows)
    if cost_basis:
        basket_return = unrealized_pl / cost_basis
    else:
        basket_return = sum(_float(row.get("unrealized_plpc")) for row in rows) / len(rows)

    n_positions = len(rows)
    daily_loss_pct = (float(daily_realized_pnl) / float(account_equity)) if account_equity else 0.0
    hard_daily_loss = account_equity > 0 and daily_loss_pct <= cfg.small_book_loss_floor_pct
    reason = ""
    reason_text = ""
    paused = False
    if hard_daily_loss:
        paused = True
        reason = "hard_daily_loss_pause"
        reason_text = f"New entries paused - daily realized loss exceeds {cfg.small_book_loss_floor_pct:.1%} of equity"
    elif n_positions < cfg.min_positions_for_percentage_rule:
        if basket_return < cfg.small_book_basket_return_floor_pct:
            paused = True
            reason = "small_book_basket_return_pause"
            reason_text = (
                f"New entries paused - basket return {basket_return:.1%} "
                f"(below {cfg.small_book_basket_return_floor_pct:.1%} threshold on a {n_positions}-position book)"
            )
    elif red_pct > cfg.pause_new_entries_if_red_position_pct_above and basket_return < cfg.pause_new_entries_if_basket_return_below:
        paused = True
        reason = "red_position_pct_pause"
        reason_text = (
            f"New entries paused - {red_pct:.0%} of {n_positions} positions in the red and "
            f"basket return {basket_return:.1%} (below {cfg.pause_new_entries_if_basket_return_below:.2%} threshold)"
        )
    elif previous_state == "new_entries_paused" and basket_return <= cfg.resume_new_entries_if_basket_return_above:
        paused = True
        reason = "basket_drawdown_pause_not_resumed"
        reason_text = f"New entries paused - basket return {basket_return:.1%} has not recovered above {cfg.resume_new_entries_if_basket_return_above:.2%}"

    return BasketRiskState(
        basket_state="new_entries_paused" if paused else "normal",
        red_position_pct=red_pct,
        basket_return=basket_return,
        new_entries_paused=paused,
        reason=reason,
        reason_text=reason_text,
        open_position_count=n_positions,
        daily_realized_loss_pct=daily_loss_pct,
    )
