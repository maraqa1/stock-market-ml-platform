from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MetaLabelGateConfig:
    enabled: bool = True
    min_meta_label_probability: float = 0.60
    transaction_cost_bps: float = 10.0


def _decision_grade_pass(row: pd.Series) -> bool:
    for column in ["model_status", "decision_grade"]:
        if column in row.index and str(row.get(column) or "").strip():
            return str(row.get(column)).strip().lower() == "decision_grade"
    return True


def evaluate_meta_label_gate(row: pd.Series, config: MetaLabelGateConfig | None = None, risk_gate_passed: bool = True) -> tuple[bool, str]:
    cfg = config or MetaLabelGateConfig()
    if not cfg.enabled:
        return True, "meta_label_gate_disabled"
    action = str(row.get("trade_action") or "").strip().lower()
    if action not in {"long", "short"}:
        return False, "primary_signal_not_long_or_short"
    if not _decision_grade_pass(row):
        return False, "model_not_decision_grade"
    probability = pd.to_numeric(pd.Series([row.get("meta_label_probability")]), errors="coerce").iloc[0]
    if pd.isna(probability):
        return False, "meta_label_probability_missing"
    if float(probability) < cfg.min_meta_label_probability:
        return False, "meta_label_probability_below_threshold"
    expected = pd.to_numeric(pd.Series([row.get("expected_trade_return")]), errors="coerce").fillna(0).iloc[0]
    cost = float(cfg.transaction_cost_bps) / 10_000.0
    if float(expected) <= cost:
        return False, "expected_trade_return_below_transaction_cost"
    if not risk_gate_passed:
        return False, "risk_gate_failed"
    return True, "meta_label_gate_passed"


def apply_meta_label_gate(frame: pd.DataFrame, config: MetaLabelGateConfig | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    cfg = config or MetaLabelGateConfig()
    out = frame.copy()
    if "meta_label_probability" not in out.columns:
        out["meta_label_probability"] = pd.NA
        out["meta_label_decision"] = "Not Available"
        out["meta_label_reason"] = "meta_label_probability_missing"
        return out
    decisions = []
    reasons = []
    for _, row in out.iterrows():
        risk_passed = bool(row.get("order_eligible", True)) and str(row.get("trade_quality_status", "approved")).lower() in {"approved", "reduced"}
        passed, reason = evaluate_meta_label_gate(row, cfg, risk_gate_passed=risk_passed)
        decisions.append("Take Trade" if passed else "Skip Trade")
        reasons.append(reason)
    out["meta_label_decision"] = decisions
    out["meta_label_reason"] = reasons
    blocked = out["meta_label_decision"].ne("Take Trade")
    if blocked.any():
        existing = out.get("trade_quality_reason", pd.Series("", index=out.index)).astype(str)
        out.loc[blocked, "trade_quality_status"] = "rejected"
        out.loc[blocked, "trade_quality_reason"] = [
            "|".join([part for part in [old, reason] if part])
            for old, reason in zip(existing.loc[blocked], out.loc[blocked, "meta_label_reason"])
        ]
        out.loc[blocked, "approved_notional"] = 0.0
        out.loc[blocked, "suggested_quantity"] = 0
        out.loc[blocked, "order_eligible"] = False
    return out
