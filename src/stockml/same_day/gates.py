from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.intraday import kill_switch
from stockml.trading.spread_edge import evaluate_spread_edge, expected_move_bps_from


CONFIG_PATH = PROJECT_ROOT / "config" / "same_day.yaml"


@dataclass(frozen=True)
class SameDayGateConfig:
    min_avg_dollar_volume_20d: float = 20_000_000
    min_price: float = 5
    max_price: float = 500
    min_market_cap: float = 500_000_000
    max_spread_bps: float = 15
    max_spread_bps_zscore_20d: float = 2.0
    estimated_cost_bps: float = 10.0
    min_edge_to_spread_ratio: float = 3.0
    min_expected_net_edge_bps: float = 25.0
    min_signal_age_seconds: int = 300
    max_signal_age_seconds: int = 3600
    min_continuation_probability: float = 0.60
    max_reversal_probability: float = 0.35
    sector_alignment_tolerance_pct: float = 0.5
    max_symbol_attempts_today: int = 2
    max_daily_candidates: int = 20


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str = ""
    gate: str = ""
    details: dict[str, Any] | None = None


def load_config(path: Path | str = CONFIG_PATH) -> SameDayGateConfig:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    gates = ((payload.get("same_day") or {}).get("gates") or {})
    return SameDayGateConfig(
        min_avg_dollar_volume_20d=float(gates.get("min_avg_dollar_volume_20d", 20_000_000)),
        min_price=float(gates.get("min_price", 5)),
        max_price=float(gates.get("max_price", 500)),
        min_market_cap=float(gates.get("min_market_cap", 500_000_000)),
        max_spread_bps=float(gates.get("max_spread_bps", 15)),
        max_spread_bps_zscore_20d=float(gates.get("max_spread_bps_zscore_20d", 2.0)),
        estimated_cost_bps=float(gates.get("estimated_cost_bps", 10.0)),
        min_edge_to_spread_ratio=float(gates.get("min_edge_to_spread_ratio", 3.0)),
        min_expected_net_edge_bps=float(gates.get("min_expected_net_edge_bps", 25.0)),
        min_signal_age_seconds=int(gates.get("min_signal_age_seconds", 300)),
        max_signal_age_seconds=int(gates.get("max_signal_age_seconds", 3600)),
        min_continuation_probability=float(gates.get("min_continuation_probability", 0.60)),
        max_reversal_probability=float(gates.get("max_reversal_probability", 0.35)),
        sector_alignment_tolerance_pct=float(gates.get("sector_alignment_tolerance_pct", 0.5)),
        max_symbol_attempts_today=int(gates.get("max_symbol_attempts_today", 2)),
        max_daily_candidates=int(gates.get("max_daily_candidates", 20)),
    )


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        if value in [None, ""]:
            return default
        return float(value)
    except Exception:
        return default


def _bool(value: Any, default: bool = False) -> bool:
    if value in [None, ""]:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return default


def _blocked(gate: str, reason: str, **details: Any) -> GateResult:
    return GateResult(False, reason=reason, gate=gate, details=details)


def evaluate(
    features: dict[str, Any],
    *,
    direction: str,
    continuation_probability: float,
    reversal_probability: float,
    config: SameDayGateConfig | None = None,
    same_day_attempts_today_for_symbol: int = 0,
    same_day_candidates_today_count: int = 0,
    kill_switch_gate: Callable[..., kill_switch.KillSwitchVerdict] = kill_switch.gate,
    engine: Any | None = None,
    now: Any | None = None,
) -> GateResult:
    cfg = config or load_config()
    avg_dollar_volume = _num(features.get("avg_dollar_volume_20d") or features.get("average_dollar_volume_20d"), 0)
    if avg_dollar_volume is None or avg_dollar_volume < cfg.min_avg_dollar_volume_20d:
        return _blocked("liquidity", "REJECTED_LIQUIDITY_THIN", observed=avg_dollar_volume, threshold=cfg.min_avg_dollar_volume_20d)

    price = _num(features.get("last_price") or features.get("close") or features.get("current_price"), 0)
    if price is None or price < cfg.min_price or price > cfg.max_price:
        return _blocked("price_band", "REJECTED_PRICE_BAND", observed=price, min_price=cfg.min_price, max_price=cfg.max_price)

    market_cap = _num(features.get("market_cap"), 0)
    if market_cap is None or market_cap < cfg.min_market_cap:
        return _blocked("market_cap", "REJECTED_MARKETCAP_MIN", observed=market_cap, threshold=cfg.min_market_cap)

    spread_bps = _num(features.get("spread_bps"), 0)
    spread_z = _num(features.get("spread_bps_zscore_20d"), 0)
    if (spread_bps is not None and spread_bps > cfg.max_spread_bps) or (spread_z is not None and spread_z > cfg.max_spread_bps_zscore_20d):
        spread_edge = evaluate_spread_edge(
            spread_bps=spread_bps,
            max_spread_bps=cfg.max_spread_bps,
            expected_move_bps=expected_move_bps_from(features),
            estimated_cost_bps=cfg.estimated_cost_bps,
            min_edge_to_spread_ratio=cfg.min_edge_to_spread_ratio,
            min_expected_net_edge_bps=cfg.min_expected_net_edge_bps,
        )
        if not spread_edge.allowed:
            return _blocked("spread", "REJECTED_WIDE_SPREAD", spread_bps_zscore_20d=spread_z, **spread_edge.details())

    if _bool(features.get("is_first_15_min")) or _bool(features.get("is_last_30_min")):
        return _blocked("time_of_day", "REJECTED_TIME_OF_DAY")

    signal_age = _num(features.get("seconds_since_signal_first_fired"))
    if signal_age is None or signal_age < cfg.min_signal_age_seconds:
        return _blocked("signal_freshness", "REJECTED_SIGNAL_FRESH", observed=signal_age, threshold=cfg.min_signal_age_seconds)
    if signal_age > cfg.max_signal_age_seconds:
        return _blocked("signal_freshness", "REJECTED_SIGNAL_STALE", observed=signal_age, threshold=cfg.max_signal_age_seconds)

    if _bool(features.get("is_halted")):
        return _blocked("halts_and_catalysts", "REJECTED_HALTED")
    if _bool(features.get("earnings_today")):
        return _blocked("halts_and_catalysts", "REJECTED_EARNINGS_TODAY")
    if _bool(features.get("earnings_yesterday")) and (_num(features.get("seconds_to_open"), 999_999) or 999_999) < 7200:
        return _blocked("halts_and_catalysts", "REJECTED_EARNINGS_RECENT")

    if continuation_probability < cfg.min_continuation_probability:
        return _blocked("continuation_probability", "REJECTED_CONTINUATION_THRESHOLD", observed=continuation_probability, threshold=cfg.min_continuation_probability)
    if reversal_probability > cfg.max_reversal_probability:
        return _blocked("continuation_probability", "REJECTED_REVERSAL_RISK_TOO_HIGH", observed=reversal_probability, threshold=cfg.max_reversal_probability)

    if not _bool(features.get("market_aligned")):
        return _blocked("market_alignment", "REJECTED_MARKET_MISALIGNED")
    sector_move = _num(features.get("sector_etf_intraday_move_pct"), 0)
    if not _bool(features.get("sector_aligned")) and (sector_move is None or abs(sector_move) > cfg.sector_alignment_tolerance_pct):
        return _blocked("sector_alignment", "REJECTED_SECTOR_MISALIGNED", sector_etf_intraday_move_pct=sector_move)

    if same_day_attempts_today_for_symbol > cfg.max_symbol_attempts_today:
        return _blocked("symbol_activity", "REJECTED_SYMBOL_ACTIVITY_LIMIT", observed=same_day_attempts_today_for_symbol, threshold=cfg.max_symbol_attempts_today)

    if same_day_candidates_today_count >= cfg.max_daily_candidates:
        return _blocked("daily_candidate_cap", "REJECTED_DAILY_CANDIDATE_CAP", observed=same_day_candidates_today_count, threshold=cfg.max_daily_candidates)

    if str(direction).lower() == "short" and not _bool(features.get("borrow_available"), True):
        return _blocked("short_borrow", "REJECTED_NO_BORROW")

    verdict = kill_switch_gate(action="evaluate", engine=engine, now=now)
    if not verdict.allow:
        return _blocked("kill_switch", "BLOCKED_KILL_SWITCH", tripped=verdict.tripped)

    return GateResult(True, details={})
