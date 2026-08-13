from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, TypeVar, get_args, get_origin


class EntryAction(str, Enum):
    ENTER = "ENTER"
    ENTER_REDUCED = "ENTER_REDUCED"
    REFRESH_AND_RECHECK = "REFRESH_AND_RECHECK"
    BLOCK = "BLOCK"


class ExitAction(str, Enum):
    HOLD = "HOLD"
    SCALE_DOWN = "SCALE_DOWN"
    TAKE_PROFIT = "TAKE_PROFIT"
    MOVE_STOP = "MOVE_STOP"
    TRAIL = "TRAIL"
    EXIT = "EXIT"


T = TypeVar("T", bound="SerializableModel")


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _coerce_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split("|") if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


def _coerce_value(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in {tuple, list}:
        return _coerce_sequence(value)
    if annotation is EntryAction:
        return EntryAction(value)
    if annotation is ExitAction:
        return ExitAction(value)
    if annotation is datetime and isinstance(value, str):
        return datetime.fromisoformat(value)
    if annotation is date and isinstance(value, str):
        return date.fromisoformat(value)
    if origin is not None and type(None) in args:
        inner = next((arg for arg in args if arg is not type(None)), Any)
        return _coerce_value(inner, value)
    return value


class SerializableModel:
    required_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        missing = [name for name in self.required_fields if _missing(getattr(self, name, None))]
        if missing:
            raise ValueError(f"missing_required_fields:{','.join(missing)}")

    def to_dict(self) -> dict[str, Any]:
        return {field.name: _serialize(getattr(self, field.name)) for field in fields(self)}

    @classmethod
    def from_dict(cls: type[T], payload: dict[str, Any]) -> T:
        values: dict[str, Any] = {}
        for field_def in fields(cls):
            if field_def.name in payload:
                values[field_def.name] = _coerce_value(field_def.type, payload[field_def.name])
        return cls(**values)


@dataclass
class Candidate(SerializableModel):
    symbol: str
    side: str
    rank: int
    candidate_status: str
    ai2_status: str
    decision_label: str
    approved_notional: float
    qty: float
    risk_class: str
    latest_eod_date: str
    close_price: float
    expected_return_bps: float
    one_day_return: float
    five_day_return: float
    twenty_day_volatility: float
    eod_volume: float
    price_check_clear: bool
    warning_codes: tuple[str, ...] = field(default_factory=tuple)
    signal_id: str = ""
    candidate_id: str = ""
    event_id: str = ""
    source_file: str = ""

    required_fields = ("symbol", "side", "rank", "candidate_status", "signal_id", "candidate_id", "event_id", "source_file")

    def __post_init__(self) -> None:
        self.warning_codes = _coerce_sequence(self.warning_codes)
        super().__post_init__()


@dataclass
class EntryDecision(SerializableModel):
    symbol: str
    action: EntryAction
    reason: str
    candidate_id: str
    signal_id: str
    event_id: str
    confidence: float = 0.0
    supporting_reasons: tuple[str, ...] = field(default_factory=tuple)
    qty: int = 0
    notional: float = 0.0
    risk_profile: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    source_file: str = ""

    required_fields = ("symbol", "action", "reason", "candidate_id", "signal_id", "event_id")

    def __post_init__(self) -> None:
        self.action = EntryAction(self.action)
        self.supporting_reasons = _coerce_sequence(self.supporting_reasons)
        self.warnings = _coerce_sequence(self.warnings)
        super().__post_init__()


@dataclass
class TradeIntent(SerializableModel):
    symbol: str
    side: str
    decision: EntryAction
    qty: float
    max_notional: float
    signal_close: float
    live_price_at_decision: float
    stop_policy: str
    take_profit_policy: str
    max_holding_period: str
    risk_tier: str
    warnings: tuple[str, ...]
    signal_id: str
    candidate_id: str
    event_id: str
    source_file: str
    ai2_status: str = ""
    warning_codes: tuple[str, ...] = field(default_factory=tuple)

    required_fields = (
        "symbol",
        "side",
        "decision",
        "qty",
        "max_notional",
        "signal_close",
        "live_price_at_decision",
        "stop_policy",
        "take_profit_policy",
        "max_holding_period",
        "risk_tier",
        "signal_id",
        "candidate_id",
        "event_id",
        "source_file",
    )

    def __post_init__(self) -> None:
        self.decision = EntryAction(self.decision)
        self.warnings = _coerce_sequence(self.warnings)
        self.warning_codes = _coerce_sequence(self.warning_codes) or self.warnings
        super().__post_init__()


@dataclass
class ExecutionFill(SerializableModel):
    symbol: str
    side: str
    qty: float
    fill_price: float
    filled_at: str
    broker_order_id: str
    client_order_id: str
    signal_id: str
    candidate_id: str
    event_id: str

    required_fields = ("symbol", "side", "qty", "fill_price", "filled_at", "broker_order_id", "client_order_id")


@dataclass
class PositionState(SerializableModel):
    symbol: str
    side: str
    qty: float
    entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_pl_pct: float
    signal_id: str
    candidate_id: str
    event_id: str
    ai2_status_at_entry: str
    warnings_at_entry: tuple[str, ...]
    risk_tier: str
    entry_decision: EntryAction
    entry_reason: str
    source_file: str
    entry_time: str = ""
    signal_close: float = 0.0
    stop_price: float = 0.0
    trailing_stop: float = 0.0
    take_profit_stage: str = "initial"
    max_price_seen: float = 0.0
    min_price_seen: float = 0.0
    max_holding_period: str = ""
    order_id: str = ""
    status: str = "open"
    current_value: float = 0.0
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0
    trailing_stop_policy: str = ""
    take_profit_policy: str = ""
    position_risk_budget: float = 0.0

    required_fields = (
        "symbol",
        "side",
        "qty",
        "entry_price",
        "signal_id",
        "candidate_id",
        "event_id",
        "ai2_status_at_entry",
        "risk_tier",
        "entry_decision",
        "entry_reason",
        "source_file",
    )

    def __post_init__(self) -> None:
        self.entry_decision = EntryAction(self.entry_decision)
        self.warnings_at_entry = _coerce_sequence(self.warnings_at_entry)
        super().__post_init__()


@dataclass
class RiskPolicy(SerializableModel):
    policy_id: str
    max_notional_per_position: float
    max_gross_exposure: float
    max_positions: int
    allow_short_selling: bool
    allow_live_execution: bool = False

    required_fields = ("policy_id", "max_notional_per_position", "max_gross_exposure", "max_positions")


@dataclass
class ExitDecision(SerializableModel):
    symbol: str
    action: ExitAction
    reason: str
    qty: float
    signal_id: str
    candidate_id: str
    event_id: str
    position_id: str = ""
    supporting_reasons: tuple[str, ...] = field(default_factory=tuple)
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0

    required_fields = ("symbol", "action", "reason", "qty", "signal_id", "candidate_id", "event_id")

    def __post_init__(self) -> None:
        self.action = ExitAction(self.action)
        self.supporting_reasons = _coerce_sequence(self.supporting_reasons)
        super().__post_init__()


@dataclass
class PortfolioSnapshot(SerializableModel):
    snapshot_at: str
    equity: float
    gross_exposure: float
    net_exposure: float
    open_positions: int
    unrealized_pl: float
    cash: float = 0.0

    required_fields = ("snapshot_at", "equity", "gross_exposure", "net_exposure", "open_positions")


@dataclass
class AuditEvent(SerializableModel):
    event_at: str
    event_type: str
    source: str
    symbol: str
    message: str
    signal_id: str = ""
    candidate_id: str = ""
    event_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    required_fields = ("event_at", "event_type", "source", "message")
