from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from stockml.trading_brain_v2.shared.config import TradingBrainConfig, load_trading_brain_config
from stockml.trading_brain_v2.shared.models import Candidate, EntryAction
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class RefreshGateDecision:
    symbol: str
    decision: str
    reason: str
    live_gap_pct: float | None = None


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


class RefreshGateBlock(PlaceholderBlock):
    block_id = "AP-B06"
    name = "Refresh Gate"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        candidate = payload.get("candidate")
        if not isinstance(candidate, Candidate):
            return BrainBlockResult(block_id=self.block_id, status="error", decision="BLOCK", reason="candidate_missing")

        decision = self.evaluate_candidate(
            candidate,
            live_price=payload.get("live_price"),
            expected_latest_eod_date=payload.get("expected_latest_eod_date"),
            session_context=payload.get("session_context"),
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
        live_price: Any = None,
        expected_latest_eod_date: Any = None,
        session_context: dict[str, Any] | None = None,
        config: TradingBrainConfig | None = None,
    ) -> RefreshGateDecision:
        cfg = config or load_trading_brain_config()
        if candidate.ai2_status == "refresh_required":
            return RefreshGateDecision(candidate.symbol, EntryAction.REFRESH_AND_RECHECK.value, "ai2_refresh_required")

        warnings = set(candidate.warning_codes)
        if "large_intraday_move" in warnings:
            return RefreshGateDecision(candidate.symbol, EntryAction.REFRESH_AND_RECHECK.value, "large_intraday_move")
        if "large_1d_move" in warnings:
            return RefreshGateDecision(candidate.symbol, EntryAction.REFRESH_AND_RECHECK.value, "large_1d_move")

        live = _float(live_price)
        close = _float(candidate.close_price)
        if live is not None and close and close > 0:
            gap = abs(live - close) / close
            if gap > cfg.max_live_gap_block_pct:
                return RefreshGateDecision(candidate.symbol, EntryAction.BLOCK.value, "live_price_gap_block", gap)
            if gap > cfg.max_live_gap_refresh_pct:
                return RefreshGateDecision(candidate.symbol, EntryAction.REFRESH_AND_RECHECK.value, "live_price_gap_refresh", gap)

        expected = expected_latest_eod_date
        if expected is None and isinstance(session_context, dict):
            expected = session_context.get("expected_latest_eod_date")
        candidate_date = _date(candidate.latest_eod_date)
        expected_date = _date(expected)
        if candidate_date is not None and expected_date is not None and candidate_date < expected_date:
            return RefreshGateDecision(candidate.symbol, EntryAction.REFRESH_AND_RECHECK.value, "latest_eod_stale")

        return RefreshGateDecision(candidate.symbol, "PASS", "refresh_gate_pass")
