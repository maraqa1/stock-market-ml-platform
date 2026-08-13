from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from stockml.trading_brain_v2.shared.models import ExitAction, ExitDecision, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


class TimeBasedExitEngineBlock(PlaceholderBlock):
    block_id = "PM-B07"
    name = "Time-Based Exit Engine"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        position = payload.get("position")
        if not isinstance(position, PositionState):
            return BrainBlockResult(block_id=self.block_id, status="error", decision=ExitAction.HOLD.value, reason="position_missing")
        decision = self.evaluate_position(
            position,
            current_time=payload.get("current_time"),
            failed_signal_minutes=float(payload.get("failed_signal_minutes", 390)),
            stale_signal=bool(payload.get("stale_signal", False)),
            signal_renewed=bool(payload.get("signal_renewed", True)),
            flat_trade_minutes=float(payload.get("flat_trade_minutes", 780)),
        )
        return BrainBlockResult(block_id=self.block_id, status="ok", decision=decision.action.value, reason=decision.reason, details=decision.to_dict())

    def evaluate_position(
        self,
        position: PositionState,
        *,
        current_time: Any = None,
        failed_signal_minutes: float = 390,
        stale_signal: bool = False,
        signal_renewed: bool = True,
        flat_trade_minutes: float = 780,
    ) -> ExitDecision:
        age_minutes = self._age_minutes(position.entry_time, current_time)
        max_hold_minutes = self._holding_period_minutes(position.max_holding_period)

        if position.unrealized_pl < 0 and age_minutes >= failed_signal_minutes:
            return self._decision(position, ExitAction.EXIT, "negative_after_failed_signal_period", age_minutes)
        if max_hold_minutes > 0 and age_minutes >= max_hold_minutes:
            return self._decision(position, ExitAction.EXIT, "max_holding_period_exceeded", age_minutes)
        if stale_signal and not signal_renewed:
            return self._decision(position, ExitAction.EXIT, "stale_signal_not_renewed", age_minutes)
        if abs(position.unrealized_pl_pct) < 0.001 and age_minutes >= flat_trade_minutes:
            return self._decision(position, ExitAction.EXIT, "flat_trade_unresolved_beyond_policy", age_minutes)
        return self._decision(position, ExitAction.HOLD, "time_exit_hold", age_minutes)

    def _age_minutes(self, entry_time: str, current_time: Any) -> float:
        start = self._parse_datetime(entry_time)
        end = self._parse_datetime(current_time) or datetime.now(timezone.utc)
        if start is None:
            return 0.0
        return max(0.0, (end - start).total_seconds() / 60.0)

    def _parse_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def _holding_period_minutes(self, value: str) -> float:
        text = str(value or "").strip().lower()
        if not text:
            return 0.0
        try:
            if text.endswith("d"):
                return float(text[:-1]) * 24 * 60
            if text.endswith("h"):
                return float(text[:-1]) * 60
            if text.endswith("m"):
                return float(text[:-1])
            return float(text)
        except ValueError:
            return 0.0

    def _decision(self, position: PositionState, action: ExitAction, reason: str, age_minutes: float) -> ExitDecision:
        return ExitDecision(
            symbol=position.symbol,
            action=action,
            reason=reason,
            qty=abs(position.qty) if action is ExitAction.EXIT else 0,
            signal_id=position.signal_id,
            candidate_id=position.candidate_id,
            event_id=position.event_id,
            supporting_reasons=(f"age_minutes={round(age_minutes, 2)}", f"max_holding_period={position.max_holding_period}"),
        )
