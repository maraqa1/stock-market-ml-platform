from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.trading.overnight_eligibility import is_overnight_halted, is_overnight_tradable
from stockml.trading.overnight_quote_quality import evaluate_quote_quality
from stockml.trading.session_mode import classify_session_mode, is_extended_session

CONFIG_PATH = PROJECT_ROOT / "config" / "session_modes.yaml"


@dataclass(frozen=True)
class SessionOrderDecision:
    allowed: bool
    session_mode: str
    order_policy: str
    order_type: str
    extended_hours: bool
    size_multiplier: float
    max_spread_bps: float | None
    session_reject_reason: str = ""
    spread_bps: float | None = None
    quote_freshness_seconds: float | None = None
    executable_price: float | None = None
    reference_price: float | None = None
    executable_price_deviation_bps: float | None = None


def load_session_mode_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or CONFIG_PATH
    if not cfg_path.exists():
        return {"session_modes": {}}
    try:
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"session_modes": {}}
    return data if isinstance(data, dict) else {"session_modes": {}}


def _mode_cfg(mode: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    data = config or load_session_mode_config()
    modes = data.get("session_modes", {}) if isinstance(data, dict) else {}
    return dict(modes.get(mode, {}))


def session_order_policy(
    *,
    now: datetime | None = None,
    asset: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
    requested_order_type: str = "market",
    config: dict[str, Any] | None = None,
) -> SessionOrderDecision:
    mode = classify_session_mode(now)
    cfg = _mode_cfg(mode, config)
    allow_submission = bool(cfg.get("allow_order_submission", cfg.get("enabled", False)))
    allow_market = bool(cfg.get("allow_market_orders", False))
    max_spread = cfg.get("max_spread_bps")
    max_spread_value = float(max_spread) if max_spread is not None else None
    multiplier = float(cfg.get("position_size_multiplier", 1.0))
    order_type = "market" if requested_order_type == "market" and allow_market else "limit"
    extended = is_extended_session(mode)
    policy_name = mode

    if not allow_submission:
        return SessionOrderDecision(False, mode, policy_name, order_type, extended, multiplier, max_spread_value, "session_order_submission_disabled")
    if mode == "weekend_closed":
        return SessionOrderDecision(False, mode, policy_name, order_type, extended, multiplier, max_spread_value, "weekend_closed")
    if cfg.get("evaluation_only") is True:
        return SessionOrderDecision(False, mode, policy_name, order_type, extended, multiplier, max_spread_value, "session_evaluation_only")
    if requested_order_type == "market" and not allow_market:
        return SessionOrderDecision(False, mode, policy_name, "market", extended, multiplier, max_spread_value, "market_orders_not_allowed")
    if cfg.get("require_overnight_tradable") and not is_overnight_tradable(asset):
        return SessionOrderDecision(False, mode, policy_name, order_type, extended, multiplier, max_spread_value, "asset_not_overnight_tradable")
    if cfg.get("require_not_overnight_halted") and is_overnight_halted(asset):
        return SessionOrderDecision(False, mode, policy_name, order_type, extended, multiplier, max_spread_value, "asset_overnight_halted")
    require_fresh_quote = bool(cfg.get("quote_freshness_required", extended))
    quality = evaluate_quote_quality(
        quote or {},
        max_spread_bps=max_spread_value or 999999.0,
        max_executable_deviation_bps=cfg.get("max_executable_deviation_bps"),
        now=now,
        require_fresh_quote=require_fresh_quote,
    )
    if extended and not quality.ok:
        return SessionOrderDecision(False, mode, policy_name, order_type, extended, multiplier, max_spread_value, quality.reason, quality.spread_bps, quality.freshness_seconds, quality.executable_price, quality.reference_price, quality.executable_price_deviation_bps)
    return SessionOrderDecision(True, mode, policy_name, order_type, extended, multiplier, max_spread_value, "", quality.spread_bps, quality.freshness_seconds, quality.executable_price, quality.reference_price, quality.executable_price_deviation_bps)
