from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.shared.models import PortfolioSnapshot, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


PORTFOLIO_ALLOW = "ALLOW"
PORTFOLIO_BLOCK_NEW_ENTRIES = "BLOCK_NEW_ENTRIES"
PORTFOLIO_REDUCE_RISK = "REDUCE_RISK"
PORTFOLIO_FORCE_EXIT_INVALID = "FORCE_EXIT_INVALID"
PORTFOLIO_BLOCK_OVERSIZED_POSITION = "BLOCK_OVERSIZED_POSITION"


@dataclass(frozen=True)
class PortfolioOverlayDecision:
    action: str
    reason: str
    weakest_symbols: tuple[str, ...] = ()
    symbol: str = ""


class PortfolioRiskOverlayBlock(PlaceholderBlock):
    block_id = "PM-B08"
    name = "Portfolio Risk Overlay"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        portfolio = payload.get("portfolio")
        positions = payload.get("positions") or []
        if not isinstance(portfolio, PortfolioSnapshot):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=PORTFOLIO_REDUCE_RISK, reason="portfolio_missing")
        decision = self.evaluate_portfolio(
            portfolio,
            positions=positions,
            proposed_symbol=payload.get("proposed_symbol", ""),
            proposed_notional=float(payload.get("proposed_notional", 0.0)),
            max_open_positions=int(payload.get("max_open_positions", 10)),
            max_single_name_exposure_pct=float(payload.get("max_single_name_exposure_pct", 0.15)),
            max_daily_loss_pct=float(payload.get("max_daily_loss_pct", 0.02)),
            max_review_adjusted_exposure_pct=float(payload.get("max_review_adjusted_exposure_pct", 0.30)),
            max_high_volatility_exposure_pct=float(payload.get("max_high_volatility_exposure_pct", 0.20)),
            minimum_cash_reserve_pct=float(payload.get("minimum_cash_reserve_pct", 0.0)),
        )
        return BrainBlockResult(block_id=self.block_id, status="ok", decision=decision.action, reason=decision.reason, details=decision.__dict__)

    def evaluate_portfolio(
        self,
        portfolio: PortfolioSnapshot,
        *,
        positions: list[PositionState],
        proposed_symbol: str = "",
        proposed_notional: float = 0.0,
        max_open_positions: int = 10,
        max_single_name_exposure_pct: float = 0.15,
        max_daily_loss_pct: float = 0.02,
        max_review_adjusted_exposure_pct: float = 0.30,
        max_high_volatility_exposure_pct: float = 0.20,
        minimum_cash_reserve_pct: float = 0.0,
    ) -> PortfolioOverlayDecision:
        equity = float(portfolio.equity or 0.0)
        if equity <= 0:
            return PortfolioOverlayDecision(PORTFOLIO_REDUCE_RISK, "portfolio_equity_missing_or_non_positive")

        daily_loss_pct = float(portfolio.unrealized_pl or 0.0) / equity
        if daily_loss_pct <= -abs(max_daily_loss_pct):
            return PortfolioOverlayDecision(PORTFOLIO_REDUCE_RISK, "daily_portfolio_loss_limit_breached", self._weakest_symbols(positions))

        if int(portfolio.open_positions) >= max_open_positions:
            return PortfolioOverlayDecision(PORTFOLIO_BLOCK_NEW_ENTRIES, "max_open_positions_reached")

        if proposed_notional > 0 and proposed_notional / equity > max_single_name_exposure_pct:
            return PortfolioOverlayDecision(PORTFOLIO_BLOCK_OVERSIZED_POSITION, "single_name_exposure_too_large", symbol=proposed_symbol)

        if minimum_cash_reserve_pct > 0 and float(portfolio.cash or 0.0) / equity < minimum_cash_reserve_pct:
            return PortfolioOverlayDecision(PORTFOLIO_BLOCK_NEW_ENTRIES, "minimum_cash_reserve_not_met")

        refresh_required = [position.symbol for position in positions if position.ai2_status_at_entry == "refresh_required"]
        if refresh_required:
            return PortfolioOverlayDecision(PORTFOLIO_FORCE_EXIT_INVALID, "refresh_required_exposure_present", tuple(refresh_required))

        review_exposure = sum(abs(position.current_value or position.entry_price * abs(position.qty)) for position in positions if position.ai2_status_at_entry == "review")
        if review_exposure / equity > max_review_adjusted_exposure_pct:
            return PortfolioOverlayDecision(PORTFOLIO_BLOCK_NEW_ENTRIES, "review_adjusted_exposure_limit_reached")

        high_vol_exposure = sum(abs(position.current_value or position.entry_price * abs(position.qty)) for position in positions if "high_volatility" in set(position.warnings_at_entry))
        if high_vol_exposure / equity > max_high_volatility_exposure_pct:
            return PortfolioOverlayDecision(PORTFOLIO_BLOCK_NEW_ENTRIES, "high_volatility_exposure_limit_reached")

        return PortfolioOverlayDecision(PORTFOLIO_ALLOW, "portfolio_overlay_allows")

    def _weakest_symbols(self, positions: list[PositionState]) -> tuple[str, ...]:
        ordered = sorted(positions, key=lambda position: float(position.unrealized_pl_pct or 0.0))
        return tuple(position.symbol for position in ordered[:3])
