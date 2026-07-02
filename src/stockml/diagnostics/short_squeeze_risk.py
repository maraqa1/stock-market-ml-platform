from __future__ import annotations

from typing import Any

import pandas as pd


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        parsed = float(value)
        if pd.isna(parsed):
            return default
        return parsed
    except Exception:
        return default


def short_squeeze_risk_for_row(row: Any) -> dict[str, Any]:
    reasons: list[str] = []
    score = 0
    volatility = _num(row.get("volatility_20d") if hasattr(row, "get") else None)
    rel_volume = _num(row.get("relative_volume") if hasattr(row, "get") else None)
    if rel_volume == 0:
        rel_volume = _num(row.get("volume_ratio_20d") if hasattr(row, "get") else None)
    momentum = _num(row.get("return_5d") if hasattr(row, "get") else None)
    intraday_move = _num(row.get("intraday_return") if hasattr(row, "get") else None)
    gap = _num(row.get("gap_pct") if hasattr(row, "get") else None)
    price = _num(row.get("close") if hasattr(row, "get") else None)
    market_cap = _num(row.get("market_cap") if hasattr(row, "get") else None)
    spread = _num(row.get("spread_bps") if hasattr(row, "get") else None)

    if volatility >= 0.08:
        score += 2
        reasons.append("extreme_volatility")
    if rel_volume >= 3.0:
        score += 2
        reasons.append("high_relative_volume")
    if momentum >= 0.10:
        score += 2
        reasons.append("recent_positive_momentum")
    if intraday_move >= 0.05:
        score += 2
        reasons.append("large_intraday_move_up")
    if gap >= 0.05:
        score += 2
        reasons.append("gap_up")
    if 0 < price < 5:
        score += 1
        reasons.append("low_price")
    if 0 < market_cap < 300_000_000:
        score += 1
        reasons.append("low_market_cap")
    if spread >= 25:
        score += 1
        reasons.append("wide_spread")

    shortable = str(row.get("shortable", "") if hasattr(row, "get") else "").strip().lower()
    if shortable in {"false", "0", "no"}:
        score += 3
        reasons.append("not_shortable")
    overnight = str(row.get("overnight_tradable", "") if hasattr(row, "get") else "").strip().lower()
    if overnight in {"false", "0", "no"}:
        score += 1
        reasons.append("overnight_not_tradable")

    tier = "low"
    if score >= 5:
        tier = "high"
    elif score >= 3:
        tier = "medium"
    return {
        "short_squeeze_risk_score": score,
        "short_squeeze_risk_tier": tier,
        "short_squeeze_risk_reasons": "|".join(reasons),
    }


def build_short_squeeze_risk(candidates: pd.DataFrame) -> pd.DataFrame:
    columns = ["symbol", "short_squeeze_risk_score", "short_squeeze_risk_tier", "short_squeeze_risk_reasons"]
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for _, row in candidates.iterrows():
        action = str(row.get("trade_action") or row.get("source_trade_action") or row.get("directional_action") or "").strip().lower()
        side = str(row.get("side") or "").strip().lower()
        if action != "short" and side not in {"sell", "short"}:
            continue
        payload = {"symbol": str(row.get("symbol") or row.get("ticker") or "").upper()}
        payload.update(short_squeeze_risk_for_row(row))
        rows.append(payload)
    return pd.DataFrame(rows).reindex(columns=columns)
