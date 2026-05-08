from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from portal.services.database_reader import model_artifacts
from portal.services.latest_file_reader import file_status, latest_file, readable_reason, safe_read_csv


def _latest_signal_table(root: Optional[Path]):
    return latest_file(root, "model_outputs", "advanced_model_signal_table_*.csv")


def _model_status(root: Optional[Path]) -> pd.DataFrame:
    db_status = model_artifacts("model_status", limit=5)
    if not db_status.empty:
        return db_status
    path = latest_file(root, "model_outputs", "advanced_model_model_status_*.csv", fallback_keys=["portal_outputs"])
    return safe_read_csv(path, nrows=5)


def _normalize_signals(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    if "trade_action" not in out.columns:
        if "target_trade_label_5d" in out.columns:
            out["trade_action"] = out["target_trade_label_5d"].replace({"Neutral": "No Decision"})
        else:
            out["trade_action"] = "No Decision"
    if "signal_reason" not in out.columns:
        out["signal_reason"] = out.get("reason", "")
    if "no_decision_reason" not in out.columns:
        out["no_decision_reason"] = out.get("reason", "")
    out["signal_reason_readable"] = out["signal_reason"].apply(readable_reason)
    out["no_decision_reason_readable"] = out["no_decision_reason"].apply(readable_reason)
    return out


def signal_context(root: Optional[Path] = None) -> dict:
    signal_file = _latest_signal_table(root)
    status_file = latest_file(root, "model_outputs", "advanced_model_model_status_*.csv", fallback_keys=["portal_outputs"])
    db_signals = model_artifacts("signal_table", limit=5000)
    signals = _normalize_signals(db_signals if not db_signals.empty else safe_read_csv(signal_file, nrows=5000))
    status = _model_status(root)
    status_row = status.iloc[0].to_dict() if not status.empty else {"decision_grade": "diagnostic_only", "reason": "model_status_missing"}
    decision_grade = str(status_row.get("decision_grade", status_row.get("status", "diagnostic_only")))
    diagnostic_only = decision_grade == "diagnostic_only" or str(status_row.get("diagnostic_only", "")).lower() == "true"

    if diagnostic_only and not signals.empty:
        display = signals.iloc[0:0].copy()
    else:
        display = signals

    long_rows = display[display["trade_action"].astype(str).str.lower().eq("long")].head(50).to_dict("records") if "trade_action" in display.columns else []
    short_rows = display[display["trade_action"].astype(str).str.lower().eq("short")].head(50).to_dict("records") if "trade_action" in display.columns else []
    no_decision = signals[signals["trade_action"].astype(str).str.lower().isin(["no decision", "neutral"])].head(50).to_dict("records") if "trade_action" in signals.columns else []

    return {
        "status": status_row,
        "decision_grade": decision_grade,
        "diagnostic_only": diagnostic_only,
        "gate_reason": readable_reason(status_row.get("reason", status_row.get("gate_reason", ""))),
        "long_count": len(long_rows),
        "short_count": len(short_rows),
        "no_decision_count": len(no_decision),
        "long_rows": long_rows,
        "short_rows": short_rows,
        "no_decision_rows": no_decision,
        "empty_signal_message": "No signals passed the validation and decision gates.",
        "files": [file_status(signal_file, "Model signal table"), file_status(status_file, "Model status")],
        "data_source": "PostgreSQL" if not db_signals.empty else "CSV",
    }


def no_decision_context(root: Optional[Path] = None) -> dict:
    db_signals = model_artifacts("signal_table", limit=10000)
    signals = _normalize_signals(db_signals if not db_signals.empty else safe_read_csv(_latest_signal_table(root), nrows=10000))
    if signals.empty or "trade_action" not in signals.columns:
        rows = []
        counts = []
    else:
        nd = signals[signals["trade_action"].astype(str).str.lower().isin(["no decision", "neutral"])].copy()
        reason_col = "no_decision_reason_readable" if "no_decision_reason_readable" in nd.columns else "signal_reason_readable"
        counts = nd[reason_col].fillna("Not provided").value_counts().reset_index().to_dict("records")
        rows = nd.head(200).to_dict("records")
    return {
        "rows": rows,
        "reason_counts": counts,
        "files": [file_status(_latest_signal_table(root), "Signal table")],
        "data_source": "PostgreSQL" if not db_signals.empty else "CSV",
    }
