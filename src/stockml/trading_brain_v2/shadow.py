from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stockml.trading_brain_v2.autopilot.ap_b10_entry_decision_engine import EntryDecisionEngineBlock
from stockml.trading_brain_v2.autopilot.ap_b11_trade_intent_builder import TradeIntentBuilderBlock
from stockml.trading_brain_v2.audit.logger import build_audit_event
from stockml.trading_brain_v2.shared.config import TradingBrainConfig
from stockml.trading_brain_v2.shared.models import AuditEvent, Candidate, EntryDecision, TradeIntent


@dataclass(frozen=True)
class ShadowDecisionRow:
    symbol: str
    rank: int
    old_brain_decision: str
    v2_decision: str
    v2_action: str
    v2_qty: int
    v2_notional: float
    v2_reason: str
    ai2_status: str
    warnings: tuple[str, ...]
    signal_id: str
    candidate_id: str
    event_id: str
    source_file: str
    live_price_used: float
    signal_close_used: float


@dataclass(frozen=True)
class ShadowRunResult:
    decisions: list[ShadowDecisionRow]
    entry_decisions: list[EntryDecision]
    trade_intents: list[TradeIntent]
    audit_events: list[AuditEvent]
    comparison: dict[str, list[str]]


class TradingBrainV2ShadowRunner:
    def run(
        self,
        candidates: list[Candidate],
        *,
        live_prices: dict[str, float],
        old_brain_decisions: dict[str, str] | None = None,
        run_id: str = "shadow",
        config: TradingBrainConfig | None = None,
    ) -> ShadowRunResult:
        cfg = config or TradingBrainConfig()
        old = old_brain_decisions or {}
        rows: list[ShadowDecisionRow] = []
        decisions: list[EntryDecision] = []
        intents: list[TradeIntent] = []
        events: list[AuditEvent] = []
        engine = EntryDecisionEngineBlock()
        builder = TradeIntentBuilderBlock()
        for candidate in candidates:
            live = float(live_prices.get(candidate.symbol, candidate.close_price))
            decision = engine.decide(candidate, live_price=live)
            decisions.append(decision)
            built = builder.build_trade_intent(decision, candidate, live_price=live)
            if built.trade_intent:
                intents.append(built.trade_intent)
            rows.append(
                ShadowDecisionRow(candidate.symbol, candidate.rank, old.get(candidate.symbol, ""), decision.action.value, decision.action.value, decision.qty, decision.notional, decision.reason, candidate.ai2_status, candidate.warning_codes, candidate.signal_id, candidate.candidate_id, candidate.event_id, candidate.source_file, live, candidate.close_price)
            )
            events.append(build_audit_event(event_type="entry_decision", run_id=run_id, source_file=candidate.source_file, symbol=candidate.symbol, message=decision.reason, candidate=candidate, entry_decision=decision, config=cfg, details={"live_price": live, "live_gap": abs(live - candidate.close_price) / candidate.close_price if candidate.close_price else None}))
        return ShadowRunResult(rows, decisions, intents, events, self._comparison(rows))

    def _comparison(self, rows: list[ShadowDecisionRow]) -> dict[str, list[str]]:
        return {
            "old_trade_v2_blocks": [row.symbol for row in rows if row.old_brain_decision in {"ENTER", "BUY", "TRADE"} and row.v2_action == "BLOCK"],
            "v2_trade_old_ignores": [row.symbol for row in rows if row.v2_action in {"ENTER", "ENTER_REDUCED"} and not row.old_brain_decision],
            "both_trade": [row.symbol for row in rows if row.v2_action in {"ENTER", "ENTER_REDUCED"} and row.old_brain_decision in {"ENTER", "BUY", "TRADE"}],
            "v2_reduces": [row.symbol for row in rows if row.v2_action == "ENTER_REDUCED"],
            "v2_refreshes": [row.symbol for row in rows if row.v2_action == "REFRESH_AND_RECHECK"],
        }
