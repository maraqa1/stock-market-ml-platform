from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import EntryAction, PositionState, TradeIntent
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class InitialRiskPolicy:
    stop_pct: float
    trailing_stop_policy: str
    take_profit_policy: str
    max_holding_period: str
    position_risk_budget: float
    reason: str


class InitialRiskAttachmentBlock(PlaceholderBlock):
    block_id = "PM-B02"
    name = "Initial Risk Attachment"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        intent = payload.get("trade_intent")
        if not isinstance(position, PositionState) or not isinstance(intent, TradeIntent):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="position_or_intent_missing")
        updated = self.attach_initial_risk(position, intent, config=payload.get("config"))
        return BrainBlockResult(block_id=self.block_id, status="ok", decision="ATTACHED", reason="initial_risk_attached", details=updated.to_dict())

    def policy_for_intent(self, intent: TradeIntent, *, config: TradingBrainConfig | None = None) -> InitialRiskPolicy:
        cfg = config or load_trading_brain_config()
        warnings = set(intent.warning_codes or intent.warnings)
        if "high_volatility" in warnings:
            return InitialRiskPolicy(cfg.high_volatility_stop_pct, "tight_volatility_trail", "fast_profit_take", "1d", 0.005, "high_volatility_reduced")
        if intent.ai2_status == "refresh_required":
            return InitialRiskPolicy(0.015, "validation_first_trail", "validation_first_profit_take", "1d", 0.003, "refreshed_candidate_validation_first")
        if EntryAction(intent.decision) is EntryAction.ENTER_REDUCED:
            return InitialRiskPolicy(cfg.reduced_review_stop_pct, "tight_reduced_trail", "faster_profit_take", "2d", 0.006, "review_reduced")
        return InitialRiskPolicy(cfg.clean_proceed_stop_pct, "standard_trail", "standard_profit_take", "5d", 0.01, "clean_proceed")

    def attach_initial_risk(self, position: PositionState, intent: TradeIntent, *, config: TradingBrainConfig | None = None) -> PositionState:
        policy = self.policy_for_intent(intent, config=config)
        entry = float(position.entry_price)
        side = str(position.side or "").upper()
        if side == "SHORT":
            stop_price = round(entry * (1 + policy.stop_pct), 4)
        else:
            stop_price = round(entry * (1 - policy.stop_pct), 4)
        payload = position.to_dict()
        payload.update(
            {
                "stop_price": stop_price,
                "trailing_stop": stop_price,
                "trailing_stop_policy": policy.trailing_stop_policy,
                "take_profit_policy": policy.take_profit_policy,
                "max_holding_period": intent.max_holding_period or policy.max_holding_period,
                "position_risk_budget": policy.position_risk_budget,
                "take_profit_stage": position.take_profit_stage or "initial",
            }
        )
        return PositionState.from_dict(payload)
