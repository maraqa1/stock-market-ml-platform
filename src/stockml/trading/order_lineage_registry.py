from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from stockml.trading.lifecycle_ids import lifecycle_position_id_for, trade_id_for


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "nan", "none", "null", "<na>"} else text


@dataclass
class OrderLineageEntry:
    candidate_id: str = ""
    client_order_id: str = ""
    broker_order_id: str = ""
    position_id: str = ""
    trade_id: str = ""
    symbol: str = ""
    lineage_warning: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "candidate_id": self.candidate_id,
            "client_order_id": self.client_order_id,
            "broker_order_id": self.broker_order_id,
            "position_id": self.position_id,
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "lineage_warning": self.lineage_warning,
        }


@dataclass
class OrderLineageRegistry:
    by_candidate_id: dict[str, OrderLineageEntry] = field(default_factory=dict)
    by_client_order_id: dict[str, OrderLineageEntry] = field(default_factory=dict)
    by_broker_order_id: dict[str, OrderLineageEntry] = field(default_factory=dict)

    def register_selected(self, payload: Mapping[str, Any]) -> OrderLineageEntry:
        entry = OrderLineageEntry(
            candidate_id=_text(payload.get("candidate_id")),
            client_order_id=_text(payload.get("client_order_id")),
            symbol=_text(payload.get("symbol") or payload.get("ticker")).upper(),
        )
        return self._store(entry)

    def register_submitted(self, payload: Mapping[str, Any], *, broker_order_id: Any = "") -> OrderLineageEntry:
        existing = self.lookup(payload) or OrderLineageEntry()
        entry = OrderLineageEntry(
            candidate_id=_text(payload.get("candidate_id")) or existing.candidate_id,
            client_order_id=_text(payload.get("client_order_id")) or existing.client_order_id,
            broker_order_id=_text(broker_order_id) or _text(payload.get("broker_order_id")) or _text(payload.get("order_id")) or existing.broker_order_id,
            position_id=existing.position_id,
            trade_id=existing.trade_id,
            symbol=_text(payload.get("symbol") or payload.get("ticker")).upper() or existing.symbol,
            lineage_warning=existing.lineage_warning,
        )
        return self._store(entry)

    def register_fill(self, payload: Mapping[str, Any]) -> OrderLineageEntry:
        existing = self.lookup(payload) or OrderLineageEntry()
        broker_order_id = _text(payload.get("broker_order_id")) or _text(payload.get("order_id")) or existing.broker_order_id
        client_order_id = _text(payload.get("client_order_id")) or existing.client_order_id
        symbol = _text(payload.get("symbol") or payload.get("ticker")).upper() or existing.symbol
        position_id = _text(payload.get("position_id"))
        if position_id.lower().startswith("paper:"):
            position_id = ""
        entry = OrderLineageEntry(
            candidate_id=_text(payload.get("candidate_id")) or existing.candidate_id,
            client_order_id=client_order_id,
            broker_order_id=broker_order_id,
            position_id=position_id or lifecycle_position_id_for(symbol=symbol, broker_order_id=broker_order_id, client_order_id=client_order_id) or "",
            trade_id=_text(payload.get("trade_id")) or trade_id_for(symbol=symbol, broker_order_id=broker_order_id, client_order_id=client_order_id) or "",
            symbol=symbol,
            lineage_warning=existing.lineage_warning,
        )
        if not entry.broker_order_id:
            entry.lineage_warning = _append_warning(entry.lineage_warning, "missing_broker_order_id")
        return self._store(entry)

    def lookup(self, payload: Mapping[str, Any]) -> OrderLineageEntry | None:
        candidate_id = _text(payload.get("candidate_id"))
        client_order_id = _text(payload.get("client_order_id"))
        broker_order_id = _text(payload.get("broker_order_id")) or _text(payload.get("order_id"))
        if broker_order_id and broker_order_id in self.by_broker_order_id:
            return self.by_broker_order_id[broker_order_id]
        if client_order_id and client_order_id in self.by_client_order_id:
            return self.by_client_order_id[client_order_id]
        if candidate_id and candidate_id in self.by_candidate_id:
            return self.by_candidate_id[candidate_id]
        return None

    def _store(self, entry: OrderLineageEntry) -> OrderLineageEntry:
        if entry.candidate_id:
            self.by_candidate_id[entry.candidate_id] = entry
        if entry.client_order_id:
            self.by_client_order_id[entry.client_order_id] = entry
        if entry.broker_order_id:
            self.by_broker_order_id[entry.broker_order_id] = entry
        return entry


def _append_warning(existing: str, warning: str) -> str:
    parts = [part for part in str(existing or "").split("|") if part]
    if warning and warning not in parts:
        parts.append(warning)
    return "|".join(parts)
