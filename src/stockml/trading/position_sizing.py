from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT


def risk_tier_multiplier(risk_tier: str) -> float:
    if risk_tier in {"high_quality", "large_liquid"}:
        multiplier = 1.0
    elif risk_tier in {"medium", "mid_risk"}:
        multiplier = 0.5
    elif risk_tier == "speculative":
        multiplier = 0.25
    else:
        multiplier = 0.0
    return multiplier


def confidence_multiplier(side_probability: float) -> float:
    probability = float(side_probability or 0)
    if probability >= 0.75:
        return 1.0
    if probability >= 0.60:
        return 0.75
    if probability >= 0.50:
        return 0.50
    return 0.25


def base_notional(account_equity: float, max_position_pct: float, max_basket_notional: float, max_daily_orders: int) -> float:
    position_cap = max(0.0, float(account_equity) * float(max_position_pct))
    daily_order_cap = float(max_basket_notional) / max(1, int(max_daily_orders))
    return min(position_cap, daily_order_cap)


def approved_notional(base_notional: float, risk_tier: str, side_probability: float = 1.0) -> float:
    notional = float(base_notional) * risk_tier_multiplier(risk_tier) * confidence_multiplier(side_probability)
    return round(max(0.0, notional), 2)


def suggested_quantity(notional: float, current_price: float) -> int:
    if current_price <= 0 or notional <= 0:
        return 0
    return int(math.floor(float(notional) / float(current_price)))


@dataclass(frozen=True)
class SameDaySizingConfig:
    max_single_position_pct_of_equity: float = 0.05
    default_position_pct_of_equity: float = 0.03
    default_position_value_cap_usd: float = 100.0
    max_concurrent_positions: int = 3
    max_total_exposure_pct: float = 0.15
    min_account_equity: float = 250.0
    max_loss_per_day_usd: float = -50.0
    stop_loss_pct: float = 0.02
    atr_stop_multiple: float = 1.5
    trailing_activation_pct: float = 0.015
    trailing_giveback_pct: float = 0.007


def load_same_day_sizing_config(path: Path | None = None) -> SameDaySizingConfig:
    config_path = path or PROJECT_ROOT / "config" / "same_day.yaml"
    if not config_path.exists():
        return SameDaySizingConfig()
    try:
        import yaml  # type: ignore

        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        values = (((payload.get("same_day") or {}).get("sizing") or {}) if isinstance(payload, dict) else {})
        return SameDaySizingConfig(**{key: values[key] for key in SameDaySizingConfig.__dataclass_fields__ if key in values})
    except Exception:
        return SameDaySizingConfig()


def same_day_default_notional(account_equity: float, config: SameDaySizingConfig | None = None) -> float:
    cfg = config or SameDaySizingConfig()
    equity = float(account_equity or 0)
    if equity < cfg.min_account_equity:
        return 0.0
    return round(min(equity * cfg.default_position_pct_of_equity, cfg.default_position_value_cap_usd, equity * cfg.max_single_position_pct_of_equity), 2)


def _is_same_day(value: Any) -> bool:
    return str(value or "").strip().lower() in {"same_day_momentum", "same_day"}


def _price(row: pd.Series) -> float:
    for column in ["current_price", "close", "last_price"]:
        try:
            value = float(row.get(column) or 0)
            if value > 0:
                return value
        except Exception:
            pass
    return 0.0


def _open_same_day_positions(open_positions: pd.DataFrame | None) -> tuple[int, float]:
    if open_positions is None or open_positions.empty:
        return 0, 0.0
    stream = open_positions.get("strategy_stream", open_positions.get("trading_stream", pd.Series("", index=open_positions.index)))
    same_day = open_positions[stream.fillna("").astype(str).str.lower().isin({"same_day_momentum", "same_day"})].copy()
    if same_day.empty:
        return 0, 0.0
    exposure = 0.0
    for column in ["market_value", "notional", "approved_notional", "cost_basis"]:
        if column in same_day.columns:
            exposure = float(pd.to_numeric(same_day[column], errors="coerce").fillna(0).abs().sum())
            break
    return len(same_day), exposure


def apply_same_day_sizing(
    frame: pd.DataFrame,
    *,
    account_equity: float,
    open_positions: pd.DataFrame | None = None,
    same_day_realized_pnl_today: float = 0.0,
    config: SameDaySizingConfig | None = None,
) -> pd.DataFrame:
    if frame.empty or "strategy_stream" not in frame.columns:
        return frame
    cfg = config or load_same_day_sizing_config()
    out = frame.copy()
    open_count, open_exposure = _open_same_day_positions(open_positions)
    same_day_seen = 0
    running_exposure = open_exposure
    max_exposure = float(account_equity or 0) * cfg.max_total_exposure_pct
    for idx, row in out.iterrows():
        if not _is_same_day(row.get("strategy_stream")):
            continue
        price = _price(row)
        block_reason = ""
        notional = same_day_default_notional(account_equity, cfg)
        if float(account_equity or 0) < cfg.min_account_equity:
            block_reason = "REJECTED_SAME_DAY_EQUITY_FLOOR"
        elif float(same_day_realized_pnl_today or 0) <= cfg.max_loss_per_day_usd:
            block_reason = "REJECTED_SAME_DAY_LOSS_LIMIT"
        elif open_count + same_day_seen >= cfg.max_concurrent_positions:
            block_reason = "REJECTED_SAME_DAY_MAX_CONCURRENT"
        elif running_exposure + notional > max_exposure:
            block_reason = "REJECTED_SAME_DAY_EXPOSURE_CAP"
        quantity = suggested_quantity(notional, price)
        if not block_reason and quantity < 1:
            block_reason = "quantity_below_one"
        if block_reason:
            out.loc[idx, "approved_notional"] = 0.0
            out.loc[idx, "notional"] = 0.0
            out.loc[idx, "suggested_quantity"] = 0
            out.loc[idx, "trade_quality_status"] = "rejected"
            out.loc[idx, "candidate_status"] = "rejected"
            out.loc[idx, "trade_quality_reason"] = block_reason
            out.loc[idx, "position_sizing_reason"] = "same_day_blocked"
            out.loc[idx, "order_eligible"] = False
            continue
        out.loc[idx, "approved_notional"] = notional
        out.loc[idx, "notional"] = notional
        out.loc[idx, "suggested_quantity"] = quantity
        out.loc[idx, "position_sizing_reason"] = "same_day_momentum_sizing"
        out.loc[idx, "candidate_status"] = out.loc[idx, "trade_quality_status"]
        out.loc[idx, "max_holding_days"] = 1
        out.loc[idx, "must_flatten_at_eod"] = True
        out.loc[idx, "order_eligible"] = True
        running_exposure += notional
        same_day_seen += 1
    return out
