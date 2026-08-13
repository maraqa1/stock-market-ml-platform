from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from stockml.trading_brain_v2.shared.models import ExitDecision, PositionState
from stockml.trading_brain_v2.shared.types import BrainBlockResult, PlaceholderBlock


@dataclass(frozen=True)
class FeedbackRecord:
    symbol: str
    signal_id: str
    candidate_id: str
    event_id: str
    entry_decision: str
    entry_price: float
    exit_price: float
    holding_period: str
    max_favorable_excursion: float
    max_adverse_excursion: float
    realised_pnl: float
    pnl_pct: float
    exit_reason: str
    warning_codes: tuple[str, ...]
    ai2_status_at_entry: str
    risk_tier: str
    source_file: str

    def to_dict(self) -> dict[str, Any]:
        payload = self.__dict__.copy()
        payload["warning_codes"] = list(self.warning_codes)
        return payload


class FeedbackStoreBlock(PlaceholderBlock):
    block_id = "PM-B12"
    name = "Feedback Store"

    def evaluate(self, payload: dict[str, Any] | None = None) -> BrainBlockResult:
        payload = payload or {}
        record = self.build_record(payload["position"], payload["exit_decision"], exit_price=payload.get("exit_price"))
        path = payload.get("path")
        if path:
            self.append_record(path, record)
        return BrainBlockResult(block_id=self.block_id, status="ok", decision="FEEDBACK_STORED" if path else "FEEDBACK_BUILT", reason="feedback_record_ready", details=record.to_dict())

    def build_record(self, position: PositionState, exit_decision: ExitDecision, *, exit_price: float | None = None) -> FeedbackRecord:
        price = float(exit_price if exit_price is not None else position.current_price)
        side = str(position.side or "").upper()
        qty = abs(float(position.qty))
        pnl = round((price - position.entry_price) * qty, 2) if side != "SHORT" else round((position.entry_price - price) * qty, 2)
        pnl_pct = (price - position.entry_price) / position.entry_price if side != "SHORT" and position.entry_price else 0.0
        if side == "SHORT" and position.entry_price:
            pnl_pct = (position.entry_price - price) / position.entry_price
        return FeedbackRecord(
            symbol=position.symbol,
            signal_id=position.signal_id,
            candidate_id=position.candidate_id,
            event_id=position.event_id,
            entry_decision=position.entry_decision.value,
            entry_price=position.entry_price,
            exit_price=price,
            holding_period=position.max_holding_period,
            max_favorable_excursion=position.max_favorable_excursion,
            max_adverse_excursion=position.max_adverse_excursion,
            realised_pnl=pnl,
            pnl_pct=round(pnl_pct, 6),
            exit_reason=exit_decision.reason,
            warning_codes=tuple(position.warnings_at_entry),
            ai2_status_at_entry=position.ai2_status_at_entry,
            risk_tier=position.risk_tier,
            source_file=position.source_file,
        )

    def append_record(self, path: str | Path, record: FeedbackRecord) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
        return out

    def read_records(self, path: str | Path) -> list[dict[str, Any]]:
        target = Path(path)
        if not target.exists():
            return []
        return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
