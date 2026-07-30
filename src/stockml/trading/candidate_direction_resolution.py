from __future__ import annotations

import pandas as pd

from stockml.trading.direction_authority import AUTHORITY_COLUMNS, DirectionAuthorityConfig, apply_authority_to_row


HARD_FLOOR_REASONS = [
    "market_cap_missing",
    "market_cap_below_minimum",
    "price_below_minimum",
    "current_price_missing",
    "current_price_invalid",
    "avg_dollar_volume_missing",
    "liquidity_below_minimum",
]


def _first_reason(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    return next((part.strip() for part in text.replace(";", "|").split("|") if part.strip()), "")


def _reasons(value: object) -> list[str]:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return []
    return [part.strip() for part in text.replace(";", "|").split("|") if part.strip()]


def _text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _reduced_reason(row: pd.Series) -> str:
    volatility_tier = _text(row.get("volatility_tier")).lower()
    risk_tier = _text(row.get("risk_tier")).lower()
    approved_notional = _num(row.get("approved_notional")) or _num(row.get("notional")) or 0.0
    suggested_quantity = int(_num(row.get("suggested_quantity")) or 0)
    if volatility_tier in {"high", "extreme", "speculative"}:
        return "reduced_due_to_volatility"
    if risk_tier and risk_tier not in {"high_quality", "quality", "approved"}:
        return "reduced_due_to_risk_tier"
    if approved_notional <= 0 or suggested_quantity <= 0:
        return "reduced_due_to_low_notional"
    return "reduced_due_to_position_sizing"


def _market_cap_missing(row: pd.Series) -> bool:
    if "market_cap" not in row.index:
        return False
    try:
        return pd.isna(row.get("market_cap"))
    except Exception:
        return False


def _hard_floor_primary(row: pd.Series) -> str:
    reasons = [
        *_reasons(row.get("trade_quality_reason", "")),
        *_reasons(row.get("primary_block_reason", "")),
    ]
    if _market_cap_missing(row):
        reasons.insert(0, "market_cap_missing")
    reason_set = {reason for reason in reasons if reason}
    for reason in HARD_FLOOR_REASONS:
        if reason in reason_set:
            return reason
    return ""


def _append_reason(existing: object, reason: str) -> str:
    parts = _reasons(existing)
    if reason and reason not in parts:
        parts.append(reason)
    return "|".join(parts)


def apply_direction_authority(
    candidates: pd.DataFrame,
    *,
    config: DirectionAuthorityConfig | None = None,
) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        out = candidates.copy() if candidates is not None else pd.DataFrame()
        for column in AUTHORITY_COLUMNS:
            if column not in out.columns:
                out[column] = pd.Series(dtype="object")
        return out
    out = candidates.copy()
    if "research_only" not in out.columns:
        out["research_only"] = False
    if "primary_block_reason" not in out.columns:
        out["primary_block_reason"] = ""
    if "trade_quality_reason" not in out.columns:
        out["trade_quality_reason"] = ""
    resolved = out.apply(lambda row: apply_authority_to_row(row, config=config), axis=1)
    status = resolved.get("trade_quality_status", pd.Series("", index=resolved.index)).fillna("").astype(str).str.lower()
    primary = resolved.get("primary_block_reason", pd.Series("", index=resolved.index)).fillna("").astype(str).str.strip()
    blank_rejected = status.eq("rejected") & primary.isin(["", "nan", "None", "none", "null"])
    if blank_rejected.any():
        resolved.loc[blank_rejected, "primary_block_reason"] = resolved.loc[blank_rejected, "trade_quality_reason"].map(_first_reason)
        still_blank = resolved.loc[blank_rejected, "primary_block_reason"].fillna("").astype(str).str.strip().eq("")
        if still_blank.any():
            resolved.loc[still_blank[still_blank].index, "primary_block_reason"] = "unknown_rejection_reason"
    primary = resolved.get("primary_block_reason", pd.Series("", index=resolved.index)).fillna("").astype(str).str.strip().str.lower()
    reduced = primary.eq("reduced")
    if reduced.any():
        resolved.loc[reduced, "primary_block_reason"] = resolved.loc[reduced].apply(_reduced_reason, axis=1)
    hard_floor = resolved.apply(_hard_floor_primary, axis=1)
    has_hard_floor = hard_floor.astype(str).ne("")
    if has_hard_floor.any():
        resolved.loc[has_hard_floor, "primary_block_reason"] = hard_floor[has_hard_floor]
        resolved.loc[has_hard_floor, "trade_quality_reason"] = [
            _append_reason(existing, reason)
            for existing, reason in zip(resolved.loc[has_hard_floor, "trade_quality_reason"], hard_floor[has_hard_floor])
        ]
        resolved.loc[has_hard_floor, "trade_quality_status"] = "rejected"
        resolved.loc[has_hard_floor, "candidate_status"] = "rejected"
        resolved.loc[has_hard_floor, "order_eligible"] = False
        resolved.loc[has_hard_floor, "approved_notional"] = 0.0
        resolved.loc[has_hard_floor, "notional"] = 0.0
        resolved.loc[has_hard_floor, "suggested_quantity"] = 0
        if "final_execution_side" in resolved.columns:
            resolved.loc[has_hard_floor, "final_execution_side"] = "NONE"
    return resolved
