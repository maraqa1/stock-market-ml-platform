from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import Candidate
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class RiskProfile:
    symbol: str
    risk_score: float
    risk_tier: str
    status_multiplier: float
    volatility_multiplier: float
    momentum_multiplier: float
    risk_class_multiplier: float
    final_risk_multiplier: float
    reasons: tuple[str, ...]
    price_gap_pct: float | None = None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_multiplier(ai2_status: str) -> tuple[float, str]:
    status = (ai2_status or "unknown").strip().lower()
    if status == "proceed":
        return 1.0, "ai2_proceed"
    if status == "review":
        return 0.35, "ai2_review_reduced"
    if status == "refresh_required":
        return 0.0, "ai2_refresh_required_blocks_risk"
    return 0.0, "ai2_unknown_blocks_risk"


def _risk_class_multiplier(risk_class: str) -> tuple[float, str]:
    normalized = (risk_class or "unknown").strip().lower()
    if normalized == "high_quality":
        return 1.0, "risk_class_high_quality"
    if normalized == "medium":
        return 0.75, "risk_class_medium"
    if normalized == "high":
        return 0.25, "risk_class_high"
    return 0.5, "risk_class_unknown"


def _volatility_multiplier(volatility: Any, cfg: TradingBrainConfig) -> tuple[float, str]:
    value = _float(volatility)
    if value is None:
        return 0.5, "volatility_unknown"
    if value <= cfg.vol_20d_full_size_max:
        return 1.0, "volatility_le_3pct"
    if value <= cfg.vol_20d_75_size_max:
        return 0.75, "volatility_3_to_5pct"
    if value <= cfg.vol_20d_50_size_max:
        return 0.5, "volatility_5_to_7pct"
    if value <= cfg.vol_20d_25_size_max:
        return 0.25, "volatility_7_to_9pct"
    return 0.0, "volatility_gt_9pct_blocks"


def _momentum_multiplier(five_day_return: Any, cfg: TradingBrainConfig) -> tuple[float, str]:
    value = abs(_float(five_day_return) or 0.0)
    if value <= cfg.five_day_full_size_max:
        return 1.0, "momentum_le_10pct"
    if value <= cfg.five_day_75_size_max:
        return 0.75, "momentum_10_to_15pct"
    if value <= cfg.five_day_50_size_max:
        return 0.5, "momentum_15_to_25pct"
    if value <= cfg.five_day_25_size_max:
        return 0.25, "momentum_25_to_30pct"
    return 0.0, "momentum_gt_30pct_blocks"


def _tier(final_multiplier: float) -> str:
    if final_multiplier <= 0:
        return "blocked"
    if final_multiplier >= 0.75:
        return "normal"
    if final_multiplier >= 0.25:
        return "reduced"
    return "minimal"


class RiskScoringEngineBlock(PlaceholderBlock):
    block_id = "AP-B08"
    name = "Risk Scoring Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        candidate = payload.get("candidate")
        if not isinstance(candidate, Candidate):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="candidate_missing")

        profile = self.score_candidate(candidate, live_price=payload.get("live_price"))
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision=profile.risk_tier,
            reason="risk_profile_calculated",
            details=profile.__dict__,
        )

    def score_candidate(self, candidate: Candidate, *, live_price: Any = None, config: TradingBrainConfig | None = None) -> RiskProfile:
        cfg = config or load_trading_brain_config()
        reasons: list[str] = []
        status_multiplier, reason = _status_multiplier(candidate.ai2_status)
        reasons.append(reason)
        risk_class_multiplier, reason = _risk_class_multiplier(candidate.risk_class)
        reasons.append(reason)
        volatility_multiplier, reason = _volatility_multiplier(candidate.twenty_day_volatility, cfg)
        reasons.append(reason)
        momentum_multiplier, reason = _momentum_multiplier(candidate.five_day_return, cfg)
        reasons.append(reason)

        warnings = set(candidate.warning_codes)
        if "high_volatility" in warnings and volatility_multiplier > 0:
            volatility_multiplier = min(volatility_multiplier, 0.5)
            reasons.append("warning_high_volatility_caps_volatility_multiplier")
        if "extended_5d_momentum" in warnings and momentum_multiplier > 0:
            momentum_multiplier = min(momentum_multiplier, 0.5)
            reasons.append("warning_extended_5d_momentum_caps_momentum_multiplier")
        if {"large_intraday_move", "large_1d_move", "price_check_failed"} & warnings:
            status_multiplier = 0.0
            reasons.append("blocking_warning_sets_status_multiplier_zero")

        price_gap_pct = None
        live = _float(live_price)
        close = _float(candidate.close_price)
        if live is not None and close and close > 0:
            price_gap_pct = abs(live - close) / close
            if price_gap_pct > 0.05:
                status_multiplier = 0.0
                reasons.append("price_gap_gt_5pct_blocks_risk")
            elif price_gap_pct > 0.025:
                status_multiplier = min(status_multiplier, 0.35)
                reasons.append("price_gap_gt_2_5pct_reduces_risk")

        final_multiplier = round(
            status_multiplier * risk_class_multiplier * volatility_multiplier * momentum_multiplier,
            6,
        )
        return RiskProfile(
            symbol=candidate.symbol,
            risk_score=round(final_multiplier * 100.0, 4),
            risk_tier=_tier(final_multiplier),
            status_multiplier=status_multiplier,
            volatility_multiplier=volatility_multiplier,
            momentum_multiplier=momentum_multiplier,
            risk_class_multiplier=risk_class_multiplier,
            final_risk_multiplier=final_multiplier,
            reasons=tuple(reasons),
            price_gap_pct=price_gap_pct,
        )
