from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import TRADING_DIR, timestamp
from stockml.decisions.reason_formatter import REASON_LABELS, format_reasons
from stockml.intraday.promotion_gate import load_promotion_config
from stockml.trading.config import AlpacaConfig, alpaca_config


OUTPUT_COLUMNS = [
    "symbol",
    "side",
    "trade_action",
    "status",
    "failed_gate",
    "failed_gate_label",
    "actual_value",
    "required_value",
    "distance_to_pass",
    "distance_pct",
    "severity",
    "reason",
    "candidate_rank",
    "side_probability",
    "probability_edge",
    "expected_trade_return",
    "risk_adjusted_score",
    "current_price",
    "market_cap",
    "avg_dollar_volume_20d",
    "volatility_20d",
    "risk_tier",
    "liquidity_tier",
    "volatility_tier",
]

SUPPORTED_GATES = {
    "expected_trade_return_below_threshold",
    "risk_adjusted_score_below_threshold",
    "market_cap_below_minimum",
    "price_below_minimum",
    "liquidity_below_minimum",
    "volatility_extreme",
    "wide_spread",
    "probability_edge_below_threshold",
    "side_probability_below_threshold",
}

GATE_LABELS = {
    "expected_trade_return_below_threshold": "Expected return below threshold",
    "risk_adjusted_score_below_threshold": "Risk-adjusted score below threshold",
    "market_cap_below_minimum": "Market cap below minimum",
    "price_below_minimum": "Price below minimum",
    "liquidity_below_minimum": "Liquidity below minimum",
    "volatility_extreme": "Volatility extreme",
    "wide_spread": "Wide spread",
    "probability_edge_below_threshold": "Probability edge below threshold",
    "side_probability_below_threshold": "Side probability below threshold",
    "unknown": "Unknown reason",
}

READABLE_GATE_LABELS = {
    label.lower(): code
    for code, label in {**REASON_LABELS, **GATE_LABELS}.items()
    if code in SUPPORTED_GATES
}


@dataclass(frozen=True)
class GateCheck:
    actual: float | None
    required: float | None
    lower_is_better: bool = False


def _num(value: Any) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return float(parsed)


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none"} else text


def _reason_parts(row: dict[str, Any]) -> list[str]:
    text = "|".join(
        part
        for part in [
            _text(row.get("trade_quality_reason")),
            _text(row.get("no_decision_reason")),
            _text(row.get("reason")),
            _text(row.get("block_reason")),
            _text(row.get("message")),
        ]
        if part
    )
    parts: list[str] = []
    for chunk in text.replace(";", "|").split("|"):
        clean = chunk.strip()
        if clean and clean.lower() not in {"approved", "reduced"}:
            parts.append(clean)
    return list(dict.fromkeys(parts))


def _reason_gate(reason: str) -> str | None:
    clean = reason.strip()
    if clean in SUPPORTED_GATES:
        return clean
    return READABLE_GATE_LABELS.get(clean.lower())


def _absolute_edge(row: dict[str, Any]) -> float | None:
    value = _num(row.get("probability_edge"))
    return abs(value) if value is not None else None


def _is_short(row: dict[str, Any]) -> bool:
    side = _text(row.get("side")).lower()
    action = _text(row.get("trade_action")).lower()
    return side == "sell" or action == "short"


def _directional_num(row: dict[str, Any], key: str) -> float | None:
    value = _num(row.get(key))
    if value is None:
        return None
    return abs(value) if _is_short(row) else value


def _gate_check(gate: str, row: dict[str, Any], config: AlpacaConfig) -> GateCheck | None:
    if gate == "expected_trade_return_below_threshold":
        return GateCheck(_directional_num(row, "expected_trade_return"), config.min_expected_trade_return)
    if gate == "risk_adjusted_score_below_threshold":
        return GateCheck(_directional_num(row, "risk_adjusted_score"), config.min_risk_adjusted_score)
    if gate == "market_cap_below_minimum":
        return GateCheck(_num(row.get("market_cap")), config.min_market_cap)
    if gate == "price_below_minimum":
        return GateCheck(_num(row.get("current_price") or row.get("close") or row.get("last_price")), config.min_trade_price)
    if gate == "liquidity_below_minimum":
        return GateCheck(_num(row.get("avg_dollar_volume_20d") or row.get("dollar_volume_today")), config.min_avg_dollar_volume_20d)
    if gate == "volatility_extreme":
        return GateCheck(_num(row.get("volatility_20d") or row.get("volatility_60d")), 0.12, lower_is_better=True)
    if gate == "wide_spread":
        return GateCheck(_num(row.get("spread_bps")), load_promotion_config().max_spread_bps, lower_is_better=True)
    if gate == "probability_edge_below_threshold":
        return GateCheck(_absolute_edge(row), config.min_abs_probability_edge)
    if gate == "side_probability_below_threshold":
        return GateCheck(_num(row.get("side_probability")), config.min_side_probability)
    return None


def _distance(check: GateCheck | None) -> tuple[float | None, float | None]:
    if check is None or check.actual is None or check.required is None:
        return None, None
    if check.lower_is_better:
        distance = max(0.0, check.actual - check.required)
    else:
        distance = max(0.0, check.required - check.actual)
    denominator = abs(check.required) if check.required else 1.0
    return distance, distance / denominator


def _severity(distance_pct: float | None) -> str:
    if distance_pct is None:
        return "unknown"
    if distance_pct <= 0.10:
        return "near_miss"
    if distance_pct <= 0.25:
        return "moderate_gap"
    return "hard_fail"


def _analysis_row(row: dict[str, Any], gate: str, config: AlpacaConfig) -> dict[str, Any]:
    check = _gate_check(gate, row, config)
    distance, distance_pct = _distance(check)
    return {
        "symbol": _text(row.get("symbol") or row.get("ticker")).upper(),
        "side": _text(row.get("side")),
        "trade_action": _text(row.get("trade_action")),
        "status": _text(row.get("trade_quality_status") or row.get("status") or row.get("basket_status")),
        "failed_gate": gate,
        "failed_gate_label": GATE_LABELS.get(gate, format_reasons(gate)),
        "actual_value": check.actual if check else None,
        "required_value": check.required if check else None,
        "distance_to_pass": distance,
        "distance_pct": distance_pct,
        "severity": _severity(distance_pct),
        "reason": format_reasons("|".join(_reason_parts(row))) if _reason_parts(row) else "",
        "candidate_rank": row.get("candidate_rank") or row.get("rank"),
        "side_probability": row.get("side_probability"),
        "probability_edge": row.get("probability_edge"),
        "expected_trade_return": row.get("expected_trade_return"),
        "risk_adjusted_score": row.get("risk_adjusted_score"),
        "current_price": row.get("current_price") or row.get("close") or row.get("last_price"),
        "market_cap": row.get("market_cap"),
        "avg_dollar_volume_20d": row.get("avg_dollar_volume_20d") or row.get("dollar_volume_today"),
        "volatility_20d": row.get("volatility_20d") or row.get("volatility_60d"),
        "risk_tier": row.get("risk_tier"),
        "liquidity_tier": row.get("liquidity_tier"),
        "volatility_tier": row.get("volatility_tier"),
    }


def near_miss_rows(frames: list[pd.DataFrame], config: AlpacaConfig | None = None) -> pd.DataFrame:
    config = config or alpaca_config()
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for frame in frames:
        if frame is None or frame.empty:
            continue
        for row in frame.fillna("").to_dict("records"):
            reasons = _reason_parts(row)
            gates = [gate for reason in reasons if (gate := _reason_gate(reason))]
            status = _text(row.get("trade_quality_status") or row.get("status") or row.get("basket_status")).lower()
            eligible = _text(row.get("order_eligible")).lower()
            if not gates and reasons:
                gates = ["unknown"]
            if not gates and status in {"rejected", "trimmed", "block"}:
                gates = ["unknown"]
            if eligible == "true" or status in {"approved", "open", "filled"}:
                continue
            for gate in gates:
                symbol = _text(row.get("symbol") or row.get("ticker")).upper()
                key = (symbol, gate)
                if key in seen:
                    continue
                seen.add(key)
                records.append(_analysis_row(row, gate, config))
    if not records:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return pd.DataFrame(records, columns=OUTPUT_COLUMNS)


def write_near_miss_analysis(frame: pd.DataFrame, output_dir: Path | None = None, stamp: str | None = None) -> Path:
    directory = output_dir or (TRADING_DIR / "near_miss")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"near_miss_{stamp or timestamp()}.csv"
    frame.reindex(columns=OUTPUT_COLUMNS).to_csv(path, index=False)
    return path
