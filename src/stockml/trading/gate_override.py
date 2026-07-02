from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.trading.config import AlpacaConfig, alpaca_config


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def paper_allow_all_override_active(root: Path | str | None = None, *, config: AlpacaConfig | None = None) -> bool:
    base = Path(root) if root is not None else PROJECT_ROOT
    cfg = config or alpaca_config()
    autopilot = _read_yaml(base / "config" / "autopilot.yaml")
    trading = _read_yaml(base / "config" / "trading.yaml")
    sessions = _read_yaml(base / "config" / "session_modes.yaml")
    section = autopilot.get("autopilot", {}) if isinstance(autopilot.get("autopilot"), dict) else {}
    anti_churn = autopilot.get("anti_churn", {}) if isinstance(autopilot.get("anti_churn"), dict) else {}
    lifecycle = autopilot.get("position_lifecycle", {}) if isinstance(autopilot.get("position_lifecycle"), dict) else {}
    trading_section = trading.get("trading", {}) if isinstance(trading.get("trading"), dict) else {}
    session_modes = sessions.get("session_modes", {}) if isinstance(sessions.get("session_modes"), dict) else {}
    overnight = session_modes.get("overnight_24_5", {}) if isinstance(session_modes.get("overnight_24_5"), dict) else {}
    return (
        cfg.submit_orders
        and cfg.paper_trading_enabled
        and not cfg.live_trading_enabled
        and _bool(section.get("open_enabled"), False)
        and not _bool(section.get("validation_mode"), True)
        and not _bool(section.get("holding_review_gate_enabled"), True)
        and not _bool(anti_churn.get("enabled"), True)
        and not _bool(lifecycle.get("require_exit_confirmation"), True)
        and _bool(trading_section.get("paper_trading_enabled"), True)
        and not _bool(trading_section.get("live_trading_enabled"), False)
        and _bool(overnight.get("allow_order_submission"), False)
        and not _bool(overnight.get("require_overnight_tradable"), True)
    )
