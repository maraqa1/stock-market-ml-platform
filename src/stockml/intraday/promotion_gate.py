from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.intraday.block_reasons import BlockReason


CONFIG_PATH = PROJECT_ROOT / "config" / "promotion.yaml"
PROMOTION_RULES = (
    "status_ok",
    "nightly_bias_present",
    "spread_within_limit",
    "liquidity_sufficient",
    "fresh_quote",
    "not_near_open",
    "not_near_close",
    "regime_not_extreme",
    "symbol_cooloff_clear",
    "long_trend_5m_positive",
    "long_trend_15m_positive",
    "long_above_vwap_floor",
    "long_range_position_confirmed",
    "long_market_aligned",
    "short_trend_5m_negative",
    "short_trend_15m_negative",
    "short_below_vwap_ceiling",
    "short_range_position_confirmed",
    "short_market_aligned",
    "score_trend_5m_bonus",
    "score_trend_15m_bonus",
    "score_volume_bonus",
    "score_range_bonus",
    "score_sector_bonus",
    "score_volatility_penalty",
    "score_spread_penalty",
    "score_vwap_penalty",
)


@dataclass(frozen=True)
class PromotionConfig:
    selection_threshold: float = 0.55
    strong_selection_threshold: float = 0.65
    symbol_cooloff_minutes: int = 60


@dataclass(frozen=True)
class PromotionGateResult:
    blocked: bool
    block_reason: BlockReason | None
    confirmed: bool
    contributing: list[str]


def load_promotion_config(path: Path | str = CONFIG_PATH) -> PromotionConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    section = payload.get("promotion") or payload
    return PromotionConfig(
        selection_threshold=float(section.get("selection_threshold", 0.55)),
        strong_selection_threshold=float(section.get("strong_selection_threshold", 0.65)),
        symbol_cooloff_minutes=int(section.get("symbol_cooloff_minutes", 60)),
    )


def _float(row: dict[str, Any], key: str, default: float | None = None) -> float | None:
    try:
        value = row.get(key, default)
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _bool_detail(row: dict[str, Any], key: str) -> bool:
    details = row.get("details") or {}
    return bool(details.get(key))


def _block(reason: BlockReason, contributing: list[str]) -> PromotionGateResult:
    return PromotionGateResult(True, reason, False, contributing)


def evaluate_promotion_gate(row: dict[str, Any], *, recent_action_taken: bool = False) -> PromotionGateResult:
    contributing: list[str] = []
    if str(row.get("status") or "ok") != "ok":
        return _block(BlockReason.MISC, ["status_not_ok"])
    contributing.append("status_ok")

    if _bool_detail(row, "is_halted"):
        return _block(BlockReason.HALTED, contributing + ["halted"])
    if _bool_detail(row, "has_earnings_today"):
        return _block(BlockReason.EARNINGS_TODAY, contributing + ["earnings_today"])
    if _bool_detail(row, "has_earnings_after_close"):
        return _block(BlockReason.EARNINGS_AFTER_CLOSE, contributing + ["earnings_after_close"])
    if _bool_detail(row, "provider_divergence"):
        return _block(BlockReason.PROVIDER_DIVERGENCE, contributing + ["provider_divergence"])

    bias = str(row.get("nightly_bias") or "").lower()
    if bias not in {"long", "short"}:
        return _block(BlockReason.NIGHTLY_SIGNAL_DROPPED, contributing + ["nightly_bias_missing"])
    contributing.append("nightly_bias_present")

    if (_float(row, "quote_age_sec", 0) or 0) > 2:
        return _block(BlockReason.STALE_QUOTE, contributing + ["stale_quote"])
    contributing.append("fresh_quote")

    if _bool_detail(row, "is_first_15_min"):
        return _block(BlockReason.NEAR_OPEN, contributing + ["near_open"])
    contributing.append("not_near_open")

    if _bool_detail(row, "is_last_30_min"):
        return _block(BlockReason.NEAR_CLOSE, contributing + ["near_close"])
    contributing.append("not_near_close")

    spread = _float(row, "spread_bps", 0) or 0
    spread_z = _float(row.get("details") or {}, "spread_bps_zscore_20d", 0) or 0
    if spread > 25 or spread_z > 3:
        return _block(BlockReason.WIDE_SPREAD, contributing + ["wide_spread"])
    contributing.append("spread_within_limit")

    liquidity_ratio = _float(row, "liquidity_ratio")
    if liquidity_ratio is not None and liquidity_ratio < 0.05:
        return _block(BlockReason.LOW_LIQUIDITY, contributing + ["low_liquidity"])
    contributing.append("liquidity_sufficient")

    if str((row.get("details") or {}).get("vix_regime") or "").lower() == "extreme":
        return _block(BlockReason.REGIME_BLOCK, contributing + ["vix_extreme"])
    contributing.append("regime_not_extreme")

    if recent_action_taken:
        return _block(BlockReason.SYMBOL_COOLOFF, contributing + ["symbol_cooloff"])
    contributing.append("symbol_cooloff_clear")

    trend_5m = _float(row, "trend_5m_pct", 0) or 0
    trend_15m = _float(row, "trend_15m_pct", 0) or 0
    vwap_distance = _float(row, "distance_from_vwap_bps", 0) or 0
    range_position = _float(row, "intraday_range_position")
    market_aligned = row.get("market_aligned")
    spy_trend = _float(row.get("details") or {}, "spy_intraday_trend_5m_pct", 0) or 0

    if bias == "long":
        checks = [
            (trend_5m > 0, "long_trend_5m_positive"),
            (trend_15m > 0, "long_trend_15m_positive"),
            (vwap_distance > -50, "long_above_vwap_floor"),
            (range_position is not None and range_position > 0.4, "long_range_position_confirmed"),
            (market_aligned is True or spy_trend > -0.5, "long_market_aligned"),
        ]
    else:
        checks = [
            (trend_5m < 0, "short_trend_5m_negative"),
            (trend_15m < 0, "short_trend_15m_negative"),
            (vwap_distance < 50, "short_below_vwap_ceiling"),
            (range_position is not None and range_position < 0.6, "short_range_position_confirmed"),
            (market_aligned is True or spy_trend < 0.5, "short_market_aligned"),
        ]

    passed = [name for ok, name in checks if ok]
    return PromotionGateResult(False, None, len(passed) == len(checks), contributing + passed)
