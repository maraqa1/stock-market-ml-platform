from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class TradabilityGateDecision:
    symbol: str
    decision: str
    reason: str
    live_price: float | None = None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


class TradabilityGateBlock(PlaceholderBlock):
    block_id = "AP-B07"
    name = "Tradability Gate"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        candidate = payload.get("candidate")
        if not isinstance(candidate, Candidate):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="candidate_missing")

        decision = self.evaluate_candidate(
            candidate,
            market_snapshot=payload.get("market_snapshot"),
            config=payload.get("config"),
        )
        return BrainBlockResult(
            block_id=self.block_id,
            status="ok",
            decision=decision.decision,
            reason=decision.reason,
            details=decision.__dict__,
        )

    def evaluate_candidate(
        self,
        candidate: Candidate,
        *,
        market_snapshot: dict[str, Any] | None = None,
        config: TradingBrainConfig | None = None,
    ) -> TradabilityGateDecision:
        cfg = config or load_trading_brain_config()
        snapshot = market_snapshot or {}
        live_price = _float(snapshot.get("live_price", snapshot.get("price")))
        if live_price is None:
            return TradabilityGateDecision(candidate.symbol, EntryAction.BLOCK.value, "live_price_missing")
        if live_price <= 0:
            return TradabilityGateDecision(candidate.symbol, EntryAction.BLOCK.value, "live_price_non_positive", live_price)
        if cfg.min_price > 0 and live_price < cfg.min_price:
            return TradabilityGateDecision(candidate.symbol, EntryAction.BLOCK.value, "price_below_minimum", live_price)

        tradable = _bool(snapshot.get("tradable", snapshot.get("is_tradable")), default=True)
        if tradable is False:
            return TradabilityGateDecision(candidate.symbol, EntryAction.BLOCK.value, "broker_not_tradable", live_price)

        halted = _bool(snapshot.get("halted", snapshot.get("is_halted")), default=False)
        if halted is True:
            return TradabilityGateDecision(candidate.symbol, EntryAction.BLOCK.value, "halted", live_price)

        volume = _float(snapshot.get("volume", snapshot.get("eod_volume", candidate.eod_volume)))
        if cfg.min_volume > 0 and volume is not None and volume < cfg.min_volume:
            action = cfg.low_volume_action if cfg.low_volume_action in {EntryAction.BLOCK.value, EntryAction.ENTER_REDUCED.value} else EntryAction.ENTER_REDUCED.value
            reason = "volume_below_minimum_reduced" if action == EntryAction.ENTER_REDUCED.value else "volume_below_minimum"
            return TradabilityGateDecision(candidate.symbol, action, reason, live_price)

        return TradabilityGateDecision(candidate.symbol, "PASS", "tradability_gate_pass", live_price)
