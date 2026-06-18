from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import MODEL_OUTPUTS_DIR, PORTAL_OUTPUTS_DIR
from stockml.diagnostics.common import latest_portal, safe_read_csv, write_report, DiagnosticOutput

SIDE_MAPPING_COLUMNS = ["symbol", "trade_action", "directional_action", "broker_side", "order_action", "audit_flag", "severity", "note"]


def _text(value: object) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def normalize_action(value: object) -> str:
    text = _text(value).lower().replace("_", " ")
    if text in {"long", "buy", "buy open", "open long"}:
        return "Long"
    if text in {"short", "sell", "sell short", "open short"}:
        return "Short"
    if text in {"no decision", "none", "neutral", "hold", "watch"}:
        return "No Decision"
    if "close" in text and "short" in text:
        return "Close Short"
    if "close" in text and "long" in text:
        return "Close Long"
    return _text(value) or "Unknown"


def inverse_action(action: object, *, allow_close_inverse: bool = False) -> str:
    normalized = normalize_action(action)
    if normalized == "Long":
        return "Short"
    if normalized == "Short":
        return "Long"
    if normalized == "Close Long":
        return "Open Short" if allow_close_inverse else "No Executable Inverse"
    if normalized == "Close Short":
        return "Open Long" if allow_close_inverse else "No Executable Inverse"
    return "No Decision"


def expected_broker_side(action: object) -> str:
    normalized = normalize_action(action)
    if normalized == "Long":
        return "buy"
    if normalized == "Short":
        return "sell"
    if normalized == "Close Long":
        return "sell"
    if normalized == "Close Short":
        return "buy"
    return ""


def build_side_mapping_audit(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    if frame.empty:
        return pd.DataFrame(columns=SIDE_MAPPING_COLUMNS)
    for item in frame.fillna("").to_dict("records"):
        symbol = _text(item.get("symbol") or item.get("ticker")).upper()
        trade_action = normalize_action(item.get("trade_action") or item.get("current_trade_action") or item.get("nightly_bias"))
        directional_action = normalize_action(item.get("directional_action") or item.get("same_day_trade_action") or "")
        broker_side = _text(item.get("side") or item.get("broker_side")).lower()
        order_action = _text(item.get("order_action") or item.get("action") or item.get("decision"))
        expected = expected_broker_side(trade_action)
        flags: list[tuple[str, str]] = []
        if trade_action == "Long" and broker_side == "sell":
            flags.append(("long_mapped_to_sell", "high"))
        if trade_action == "Short" and broker_side == "buy":
            flags.append(("short_mapped_to_buy", "high"))
        if trade_action == "No Decision" and broker_side in {"buy", "sell"}:
            flags.append(("no_decision_mapped_to_order", "high"))
        if order_action.lower().startswith("close") and trade_action in {"Long", "Short"} and _text(item.get("open_or_close")).lower() == "open":
            flags.append(("close_action_mapped_as_new_open", "medium"))
        if directional_action in {"Long", "Short"} and trade_action in {"Long", "Short"} and directional_action != trade_action:
            flags.append(("directional_action_conflicts_with_trade_action", "medium"))
        if expected and broker_side and broker_side != expected and not flags:
            flags.append(("trade_action_conflicts_with_broker_side", "medium"))
        if not flags:
            flags.append(("ok", "info"))
        for flag, severity in flags:
            rows.append({"symbol": symbol, "trade_action": trade_action, "directional_action": directional_action, "broker_side": broker_side, "order_action": order_action, "audit_flag": flag, "severity": severity, "note": ""})
    return pd.DataFrame(rows, columns=SIDE_MAPPING_COLUMNS)


def build_side_mapping_audit_report(stamp: str, *, order_file: Path | None = None) -> DiagnosticOutput:
    path = order_file or latest_portal("08_alpaca_paper_order_plan_*.csv") or latest_portal("08_alpaca_paper_order_results_*.csv")
    frame = safe_read_csv(path)
    out = build_side_mapping_audit(frame)
    if out.empty:
        out = pd.DataFrame([{"symbol": "", "trade_action": "", "directional_action": "", "broker_side": "", "order_action": "", "audit_flag": "missing_data", "severity": "warning", "note": "No order/candidate input available."}])
        return write_report("side_mapping_audit", out, MODEL_OUTPUTS_DIR / "diagnostics" / f"side_mapping_audit_{stamp}.csv", missing_inputs=("order_plan_or_results",))
    return write_report("side_mapping_audit", out, MODEL_OUTPUTS_DIR / "diagnostics" / f"side_mapping_audit_{stamp}.csv")
