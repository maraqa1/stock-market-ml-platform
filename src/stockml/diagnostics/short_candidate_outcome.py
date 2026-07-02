from __future__ import annotations

from typing import Any

import pandas as pd


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _series_num(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[name], errors="coerce")


def _forward_bps(frame: pd.DataFrame, horizon: str) -> pd.Series:
    bps_col = f"forward_{horizon}_return_bps"
    raw_col = f"forward_{horizon}_return"
    target_col = f"target_return_{horizon}"
    if bps_col in frame.columns:
        return _series_num(frame, bps_col)
    for column in [raw_col, target_col]:
        if column in frame.columns:
            values = _series_num(frame, column)
            return values.where(values.abs().gt(2), values * 10_000)
    return pd.Series(float("nan"), index=frame.index, dtype="float64")


def _side_mask(frame: pd.DataFrame) -> pd.Series:
    action = frame.get("trade_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    source = frame.get("source_trade_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    directional = frame.get("directional_action", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    side = frame.get("side", pd.Series("", index=frame.index)).fillna("").astype(str).str.lower()
    return action.eq("short") | source.eq("short") | directional.eq("short") | side.isin({"sell", "short"})


def build_short_candidate_outcomes(
    candidates: pd.DataFrame,
    *,
    estimated_spread_cost_bps: float = 5.0,
    estimated_slippage_bps: float = 5.0,
    borrow_cost_estimate_bps: float = 0.0,
) -> pd.DataFrame:
    columns = [
        "symbol",
        "date",
        "model_score",
        "rank_overall",
        "predicted_rank_pct",
        "source_trade_action",
        "directional_action",
        "side",
        "forward_1d_return",
        "forward_5d_return",
        "forward_10d_return",
        "short_return_1d_bps",
        "short_return_5d_bps",
        "short_return_10d_bps",
        "estimated_spread_cost_bps",
        "estimated_slippage_bps",
        "borrow_cost_estimate_bps",
        "net_short_return_bps",
        "sector",
        "risk_tier",
        "liquidity_tier",
        "volatility_tier",
    ]
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=columns)
    frame = candidates.copy()
    shorts = frame[_side_mask(frame)].copy()
    if shorts.empty:
        return pd.DataFrame(columns=columns)

    f1 = _forward_bps(shorts, "1d")
    f5 = _forward_bps(shorts, "5d")
    f10 = _forward_bps(shorts, "10d")
    cost = float(estimated_spread_cost_bps) + float(estimated_slippage_bps) + float(borrow_cost_estimate_bps)
    out = pd.DataFrame(
        {
            "symbol": (shorts.get("symbol", shorts.get("ticker", pd.Series("", index=shorts.index))).fillna("").astype(str).str.upper()),
            "date": shorts.get("date", pd.Series("", index=shorts.index)),
            "model_score": shorts.get("model_score", pd.Series(pd.NA, index=shorts.index)),
            "rank_overall": shorts.get("rank_overall", shorts.get("raw_rank", pd.Series(pd.NA, index=shorts.index))),
            "predicted_rank_pct": shorts.get("predicted_rank_pct_by_date", shorts.get("predicted_rank_pct", pd.Series(pd.NA, index=shorts.index))),
            "source_trade_action": shorts.get("source_trade_action", shorts.get("trade_action", pd.Series("", index=shorts.index))),
            "directional_action": shorts.get("directional_action", pd.Series("", index=shorts.index)),
            "side": "Short",
            "forward_1d_return": f1 / 10_000,
            "forward_5d_return": f5 / 10_000,
            "forward_10d_return": f10 / 10_000,
            "short_return_1d_bps": -f1,
            "short_return_5d_bps": -f5,
            "short_return_10d_bps": -f10,
            "estimated_spread_cost_bps": estimated_spread_cost_bps,
            "estimated_slippage_bps": estimated_slippage_bps,
            "borrow_cost_estimate_bps": borrow_cost_estimate_bps,
            "net_short_return_bps": -f5 - cost,
            "sector": shorts.get("sector", pd.Series("", index=shorts.index)),
            "risk_tier": shorts.get("risk_tier", pd.Series("", index=shorts.index)),
            "liquidity_tier": shorts.get("liquidity_tier", pd.Series("", index=shorts.index)),
            "volatility_tier": shorts.get("volatility_tier", pd.Series("", index=shorts.index)),
        }
    )
    return out.reindex(columns=columns)


def build_inverse_long_comparison(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = ["symbol", "date", "short_net_pnl_bps", "inverse_long_net_pnl_bps", "inverse_minus_short_bps", "inverse_outperforms"]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    short_net = pd.to_numeric(outcomes.get("net_short_return_bps", 0), errors="coerce")
    costs = (
        pd.to_numeric(outcomes.get("estimated_spread_cost_bps", 0), errors="coerce").fillna(0)
        + pd.to_numeric(outcomes.get("estimated_slippage_bps", 0), errors="coerce").fillna(0)
    )
    forward = pd.to_numeric(outcomes.get("forward_5d_return", 0), errors="coerce") * 10_000
    inverse = forward - costs
    return pd.DataFrame(
        {
            "symbol": outcomes.get("symbol", pd.Series("", index=outcomes.index)),
            "date": outcomes.get("date", pd.Series("", index=outcomes.index)),
            "short_net_pnl_bps": short_net,
            "inverse_long_net_pnl_bps": inverse,
            "inverse_minus_short_bps": inverse - short_net,
            "inverse_outperforms": inverse.gt(short_net),
        }
    ).reindex(columns=columns)


def summarize_short_bucket_performance(outcomes: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "bucket",
        "group",
        "count",
        "win_rate",
        "average_short_return_bps",
        "median_short_return_bps",
        "average_win_bps",
        "average_loss_bps",
        "profit_factor",
        "net_return_after_costs_bps",
    ]
    if outcomes is None or outcomes.empty:
        return pd.DataFrame(columns=columns)
    frame = outcomes.copy()
    rank = pd.to_numeric(frame.get("rank_overall", pd.Series(pd.NA, index=frame.index)), errors="coerce")
    if rank.notna().any():
        frame["bottom_decile"] = pd.qcut(rank.rank(method="first"), q=10, labels=False, duplicates="drop")
        frame["bottom_quintile"] = pd.qcut(rank.rank(method="first"), q=5, labels=False, duplicates="drop")
    else:
        frame["bottom_decile"] = 0
        frame["bottom_quintile"] = 0

    rows: list[dict[str, Any]] = []
    for bucket, column in [
        ("bottom_decile", "bottom_decile"),
        ("bottom_quintile", "bottom_quintile"),
        ("sector", "sector"),
        ("volatility_tier", "volatility_tier"),
        ("liquidity_tier", "liquidity_tier"),
        ("risk_tier", "risk_tier"),
    ]:
        if column not in frame.columns:
            continue
        for group, part in frame.groupby(column, dropna=False):
            returns = pd.to_numeric(part["net_short_return_bps"], errors="coerce").dropna()
            if returns.empty:
                continue
            wins = returns[returns.gt(0)]
            losses = returns[returns.lt(0)]
            gross_profit = float(wins.sum())
            gross_loss = abs(float(losses.sum()))
            rows.append(
                {
                    "bucket": bucket,
                    "group": group,
                    "count": int(len(returns)),
                    "win_rate": float(returns.gt(0).mean()),
                    "average_short_return_bps": float(returns.mean()),
                    "median_short_return_bps": float(returns.median()),
                    "average_win_bps": float(wins.mean()) if len(wins) else 0.0,
                    "average_loss_bps": float(losses.mean()) if len(losses) else 0.0,
                    "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0),
                    "net_return_after_costs_bps": float(returns.sum()),
                }
            )
    return pd.DataFrame(rows).reindex(columns=columns)
