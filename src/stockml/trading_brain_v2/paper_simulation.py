from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from stockml.trading_brain_v2.autopilot.ap_b12_execution_handoff import ExecutionHandoffBlock
from stockml.trading_brain_v2.position_management.pm_b01_position_creation import PositionCreationBlock
from stockml.trading_brain_v2.position_management.pm_b03_live_mark_to_market import LiveMarkToMarketBlock
from stockml.trading_brain_v2.position_management.pm_b04_stop_loss_engine import StopLossEngineBlock
from stockml.trading_brain_v2.position_management.pm_b05_profit_taking_engine import ProfitTakingEngineBlock
from stockml.trading_brain_v2.position_management.pm_b08_portfolio_risk_overlay import PORTFOLIO_BLOCK_NEW_ENTRIES, PortfolioRiskOverlayBlock
from stockml.trading_brain_v2.position_management.pm_b11_performance_attribution import AttributionRow, PerformanceAttributionBlock
from stockml.trading_brain_v2.shared.models import ExitAction, PortfolioSnapshot, PositionState, TradeIntent


@dataclass(frozen=True)
class PaperSimulationMetrics:
    total_deployed_capital: float
    current_value: float
    realised_pnl: float
    unrealised_pnl: float
    total_pnl: float
    return_pct: float
    winners: int
    losers: int
    average_winner: float
    average_loser: float
    max_drawdown: float
    attribution: list[AttributionRow]


@dataclass(frozen=True)
class PaperSimulationResult:
    positions: list[PositionState]
    closed_positions: list[PositionState]
    metrics: PaperSimulationMetrics


class TradingBrainV2PaperSimulator:
    def open_positions(self, intents: list[TradeIntent], *, portfolio: PortfolioSnapshot | None = None) -> list[PositionState]:
        positions: list[PositionState] = []
        overlay = PortfolioRiskOverlayBlock()
        base_portfolio = portfolio or PortfolioSnapshot("simulation", 100000.0, 0.0, 0.0, 0, 0.0, 100000.0)
        for intent in intents:
            gate = overlay.evaluate_portfolio(base_portfolio, positions=positions, proposed_symbol=intent.symbol, proposed_notional=intent.max_notional)
            if gate.action == PORTFOLIO_BLOCK_NEW_ENTRIES:
                continue
            fill = ExecutionHandoffBlock().execute_intent(intent, mode="shadow").fill
            if fill:
                positions.append(PositionCreationBlock().create_position(intent, fill))
        return positions

    def apply_price_updates(self, positions: list[PositionState], prices: dict[str, float]) -> PaperSimulationResult:
        marked: list[PositionState] = []
        closed: list[PositionState] = []
        mtm = LiveMarkToMarketBlock()
        stop = StopLossEngineBlock()
        profit = ProfitTakingEngineBlock()
        for position in positions:
            updated = mtm.mark_to_market(position, current_price=prices.get(position.symbol, position.current_price))
            stop_decision = stop.evaluate_position(updated)
            profit_decision = profit.evaluate_position(updated)
            if stop_decision.action is ExitAction.EXIT or profit_decision.action in {ExitAction.TAKE_PROFIT, ExitAction.EXIT}:
                closed.append(updated)
            else:
                marked.append(updated)
        return PaperSimulationResult(marked, closed, self.metrics(marked + closed, closed))

    def metrics(self, positions: list[PositionState], closed_positions: list[PositionState] | None = None) -> PaperSimulationMetrics:
        deployed = round(sum(abs(position.qty) * position.entry_price for position in positions), 2)
        current = round(sum(position.current_value for position in positions), 2)
        unrealised = round(sum(position.unrealized_pl for position in positions), 2)
        realised = round(sum(position.unrealized_pl for position in closed_positions or []), 2)
        pnl_values = [position.unrealized_pl for position in positions]
        winners = [value for value in pnl_values if value > 0]
        losers = [value for value in pnl_values if value < 0]
        return PaperSimulationMetrics(
            deployed,
            current,
            realised,
            unrealised,
            round(realised + unrealised, 2),
            round((realised + unrealised) / deployed, 6) if deployed else 0.0,
            len(winners),
            len(losers),
            round(sum(winners) / len(winners), 2) if winners else 0.0,
            round(sum(losers) / len(losers), 2) if losers else 0.0,
            round(min([0.0] + pnl_values), 2),
            PerformanceAttributionBlock().attribute(positions),
        )

    def persist_result(self, result: PaperSimulationResult, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "positions": [position.to_dict() for position in result.positions],
            "closed_positions": [position.to_dict() for position in result.closed_positions],
            "metrics": {
                **result.metrics.__dict__,
                "attribution": [row.__dict__ for row in result.metrics.attribution],
            },
        }
        target.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        return target

    def read_result(self, path: str | Path) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8"))
