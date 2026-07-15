from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from stockml.common.paths import PROJECT_ROOT


DEFAULT_ALLOWED_SCOPES = ("ticker", "bucket", "side")


@dataclass(frozen=True)
class VolatilityOpportunityConfig:
    enabled: bool = True
    side: str = "LONG_ONLY"
    min_validated_expected_return_bps: float = 40.0
    min_validated_profit_factor: float = 1.10
    min_validated_hit_rate: float = 0.50
    min_ticker_direction_sample_count: int = 5
    require_ticker_direction_bias: str = "trust_long"
    allowed_expected_return_scopes: tuple[str, ...] = DEFAULT_ALLOWED_SCOPES


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tuple(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return tuple(_text(item).lower() for item in value if _text(item))
    text = _text(value)
    if not text:
        return default
    return tuple(part.strip().lower() for part in text.replace(";", ",").split(",") if part.strip())


def _expected_return_scope(row: Any) -> str:
    scope = _text(row.get("expected_return_scope", "") if hasattr(row, "get") else "").lower()
    if scope:
        return scope
    if _text(row.get("calibrated_bucket_id", "") if hasattr(row, "get") else ""):
        return "bucket"
    calibration_source = _text(row.get("calibration_source", "") if hasattr(row, "get") else "").lower()
    calibration_quality = _text(row.get("calibration_quality", "") if hasattr(row, "get") else "").lower()
    if calibration_quality == "usable" and "bucket" in calibration_source:
        return "bucket"
    return "unknown"


def load_volatility_opportunity_config(path: Path | str | None = None) -> VolatilityOpportunityConfig:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("volatility_opportunity", {}) if isinstance(payload, dict) else {}
    return VolatilityOpportunityConfig(
        enabled=_bool(data.get("enabled"), True),
        side=_text(data.get("side")) or "LONG_ONLY",
        min_validated_expected_return_bps=_float(data.get("min_validated_expected_return_bps"), 40.0),
        min_validated_profit_factor=_float(data.get("min_validated_profit_factor"), 1.10),
        min_validated_hit_rate=_float(data.get("min_validated_hit_rate"), 0.50),
        min_ticker_direction_sample_count=_int(data.get("min_ticker_direction_sample_count"), 5),
        require_ticker_direction_bias=_text(data.get("require_ticker_direction_bias")) or "trust_long",
        allowed_expected_return_scopes=_tuple(data.get("allowed_expected_return_scopes"), DEFAULT_ALLOWED_SCOPES),
    )


def evaluate_volatility_opportunity(
    row: Any,
    reasons: list[str],
    *,
    config: VolatilityOpportunityConfig | None = None,
) -> dict[str, Any]:
    cfg = config or load_volatility_opportunity_config()
    clean_reasons = [_text(reason).lower() for reason in reasons if _text(reason)]
    result = {
        "volatility_opportunity_status": "not_applicable",
        "volatility_opportunity_reason": "",
        "volatility_opportunity_allows_reduced_trade": False,
    }
    if not cfg.enabled:
        result.update(volatility_opportunity_status="disabled", volatility_opportunity_reason="volatility_opportunity_disabled")
        return result
    if "volatility_extreme" not in clean_reasons:
        return result
    other_reasons = [reason for reason in clean_reasons if reason != "volatility_extreme"]
    if other_reasons:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason=f"other_blocker:{other_reasons[0]}")
        return result
    if cfg.side.upper() != "LONG_ONLY":
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="unsupported_side_policy")
        return result
    source = _text(row.get("source_trade_action", "") if hasattr(row, "get") else "").lower()
    action = _text(row.get("trade_action", "") if hasattr(row, "get") else "").lower()
    side = _text(row.get("side", "") if hasattr(row, "get") else "").lower()
    if source != "long" or action != "long" or side not in {"", "buy", "long"}:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="not_source_approved_long")
        return result
    scope = _expected_return_scope(row)
    if scope not in set(cfg.allowed_expected_return_scopes):
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="expected_return_scope_not_allowed")
        return result
    expected_bps = _num(row.get("validated_expected_return_bps", "") if hasattr(row, "get") else "")
    if expected_bps is None or expected_bps < cfg.min_validated_expected_return_bps:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="validated_expected_return_too_low")
        return result
    profit_factor = _num(row.get("validated_profit_factor", "") if hasattr(row, "get") else "")
    if profit_factor is None or profit_factor < cfg.min_validated_profit_factor:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="validated_profit_factor_too_low")
        return result
    hit_rate = _num(row.get("validated_hit_rate", "") if hasattr(row, "get") else "")
    if hit_rate is None or hit_rate < cfg.min_validated_hit_rate:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="validated_hit_rate_too_low")
        return result
    sample_count = int(_num(row.get("ticker_direction_sample_count", "") if hasattr(row, "get") else "") or 0)
    if sample_count < cfg.min_ticker_direction_sample_count:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="insufficient_direction_memory")
        return result
    bias = _text(row.get("ticker_direction_bias", "") if hasattr(row, "get") else "").lower()
    if bias != cfg.require_ticker_direction_bias.lower():
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="ticker_direction_bias_not_trust_long")
        return result
    liquidity = _text(row.get("liquidity_tier", "") if hasattr(row, "get") else "").lower()
    if liquidity not in {"high", "medium"}:
        result.update(volatility_opportunity_status="blocked", volatility_opportunity_reason="liquidity_not_sufficient_for_volatility")
        return result
    result.update(
        volatility_opportunity_status="qualified_reduced",
        volatility_opportunity_reason="volatility_extreme_offset_by_validated_edge",
        volatility_opportunity_allows_reduced_trade=True,
    )
    return result
