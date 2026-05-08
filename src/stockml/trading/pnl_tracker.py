from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.common.paths import PAPER_PNL_DIR, ensure_data_dirs, timestamp


def position_pnl_summary(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty:
        return pd.DataFrame(columns=["symbol", "qty", "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc"])
    out = positions.copy()
    for column in ["qty", "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc"]:
        if column not in out.columns:
            out[column] = 0
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    if "symbol" not in out.columns:
        out["symbol"] = ""
    if "unrealized_pl" not in positions.columns:
        out["unrealized_pl"] = out["market_value"] - out["cost_basis"]
    if "unrealized_plpc" not in positions.columns:
        out["unrealized_plpc"] = out["unrealized_pl"] / out["cost_basis"].replace(0, pd.NA)
    return out[["symbol", "qty", "market_value", "cost_basis", "unrealized_pl", "unrealized_plpc"]]


def realized_trade_pnl(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["symbol", "side", "qty", "entry_price", "exit_price", "realized_pnl", "realized_return"])
    out = trades.copy()
    for column in ["qty", "entry_price", "exit_price"]:
        out[column] = pd.to_numeric(out.get(column, 0), errors="coerce").fillna(0)
    side = out.get("side", "buy").astype(str).str.lower()
    long_pnl = (out["exit_price"] - out["entry_price"]) * out["qty"]
    short_pnl = (out["entry_price"] - out["exit_price"]) * out["qty"]
    out["realized_pnl"] = long_pnl.where(side.ne("sell"), short_pnl)
    out["realized_return"] = out["realized_pnl"] / (out["entry_price"] * out["qty"]).replace(0, pd.NA)
    return out[["symbol", "side", "qty", "entry_price", "exit_price", "realized_pnl", "realized_return"]]


def write_pnl_summary(summary: pd.DataFrame, stamp: str | None = None) -> Path:
    ensure_data_dirs()
    path = PAPER_PNL_DIR / f"paper_pnl_{stamp or timestamp()}.csv"
    summary.to_csv(path, index=False)
    return path
