from __future__ import annotations

import pandas as pd

from stockml.trading.direction_authority import AUTHORITY_COLUMNS, DirectionAuthorityConfig, apply_authority_to_row


def _first_reason(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return ""
    return next((part.strip() for part in text.replace(";", "|").split("|") if part.strip()), "")


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
    return resolved
