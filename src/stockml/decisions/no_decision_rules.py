from __future__ import annotations

import pandas as pd


def no_decision_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    action = str(row.get("trade_action", "") or "").strip().lower()
    if action not in {"long", "short"}:
        reasons.append("not_long_or_short")
    raw_reason = str(row.get("no_decision_reason", "") or "").strip()
    if raw_reason and raw_reason.lower() not in {"nan", "none", "null"}:
        reasons.append("no_decision_reason_present")
    if str(row.get("diagnostic_only", "")).strip().lower() in {"true", "1", "yes"}:
        reasons.append("model_not_decision_grade")
    return reasons


def attach_no_decision_reasons(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    out["no_decision_rule_reasons"] = out.apply(lambda row: "|".join(no_decision_reasons(row)), axis=1)
    return out
