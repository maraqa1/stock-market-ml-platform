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


@dataclass(frozen=True)
class BasketRiskState:
    basket_state: str
    red_position_pct: float
    basket_return: float
    new_entries_paused: bool
    reason: str = ""


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
    )


def evaluate_basket_risk(
    positions: Iterable[dict[str, Any]],
    *,
    config: BasketRiskConfig | None = None,
    previous_state: str | None = None,
) -> BasketRiskState:
    cfg = config or BasketRiskConfig()
    rows = [dict(row) for row in positions]
    if not rows:
        return BasketRiskState("normal", 0.0, 0.0, False, "")

    red_count = sum(1 for row in rows if _float(row.get("unrealized_plpc")) < 0)
    red_pct = red_count / len(rows)
    cost_basis = sum(_float(row.get("cost_basis")) for row in rows)
    unrealized_pl = sum(_float(row.get("unrealized_pl")) for row in rows)
    if cost_basis:
        basket_return = unrealized_pl / cost_basis
    else:
        basket_return = sum(_float(row.get("unrealized_plpc")) for row in rows) / len(rows)

    reason = ""
    paused = False
    if red_pct > cfg.pause_new_entries_if_red_position_pct_above:
        paused = True
        reason = "red_position_pct_pause"
    elif basket_return < cfg.pause_new_entries_if_basket_return_below:
        paused = True
        reason = "basket_drawdown_pause"
    elif previous_state == "new_entries_paused" and basket_return <= cfg.resume_new_entries_if_basket_return_above:
        paused = True
        reason = "basket_drawdown_pause_not_resumed"

    return BasketRiskState(
        basket_state="new_entries_paused" if paused else "normal",
        red_position_pct=red_pct,
        basket_return=basket_return,
        new_entries_paused=paused,
        reason=reason,
    )
