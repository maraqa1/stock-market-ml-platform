from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.common.paths import INTERIM_DIR, RAW_DIR, latest_file
from stockml.trading.config import AlpacaConfig
from stockml.trading.position_sizing import approved_notional, suggested_quantity
from stockml.trading.risk_checks import liquidity_tier, numeric, reject_reasons, risk_tier, volatility_tier
from stockml.trading.stop_take_profit import stop_take_profit_prices


PRICE_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
SOURCE_MARKET_COLUMNS = ["close", "open", "high", "low", "volume"]
QUALITY_MARKET_COLUMNS = ["current_price", "open_price", "intraday_high", "intraday_low", "intraday_volume"]


def _has_inline_market_context(signals: pd.DataFrame) -> bool:
    source_columns_present = all(column in signals.columns for column in SOURCE_MARKET_COLUMNS)
    quality_columns_present = all(column in signals.columns for column in QUALITY_MARKET_COLUMNS)
    return source_columns_present or quality_columns_present


def latest_price_snapshot(tickers: list[str], price_file: Optional[Path] = None) -> pd.DataFrame:
    path = price_file or (RAW_DIR / "03_us_price_history_store.csv")
    if not path.exists() or not tickers:
        return pd.DataFrame()
    wanted = {str(ticker).upper() for ticker in tickers}
    rows = []
    try:
        for chunk in pd.read_csv(path, usecols=lambda col: col in PRICE_COLUMNS, chunksize=200_000, low_memory=False):
            chunk["ticker"] = chunk["ticker"].astype(str).str.upper().str.strip()
            subset = chunk[chunk["ticker"].isin(wanted)]
            if not subset.empty:
                rows.append(subset)
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    prices = pd.concat(rows, ignore_index=True)
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    return prices.sort_values(["ticker", "date"]).groupby("ticker", as_index=False).tail(1)


def latest_metadata_snapshot(metadata_file: Optional[Path] = None) -> pd.DataFrame:
    path = metadata_file or latest_file(INTERIM_DIR, "04_us_metadata_enriched_*.csv")
    if path is None or not path.exists():
        return pd.DataFrame()
    cols = ["ticker", "market_cap"]
    try:
        frame = pd.read_csv(path, usecols=lambda col: col in cols, low_memory=False)
    except Exception:
        return pd.DataFrame()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    return frame.drop_duplicates("ticker", keep="last")


def _prepare_market_context(
    signals: pd.DataFrame,
    price_snapshot: Optional[pd.DataFrame],
    metadata: Optional[pd.DataFrame],
) -> pd.DataFrame:
    out = signals.copy()
    out["ticker"] = out["ticker"].astype(str).str.upper().str.strip()
    if price_snapshot is not None and not price_snapshot.empty:
        prices = price_snapshot.copy()
        prices["ticker"] = prices["ticker"].astype(str).str.upper().str.strip()
        prices = prices.rename(
            columns={
                "open": "open_price",
                "high": "intraday_high",
                "low": "intraday_low",
                "close": "current_price",
                "volume": "intraday_volume",
            }
        )
        out = out.merge(prices[["ticker", "open_price", "intraday_high", "intraday_low", "current_price", "intraday_volume"]], on="ticker", how="left")
    for source, target in [("close", "current_price"), ("open", "open_price"), ("high", "intraday_high"), ("low", "intraday_low"), ("volume", "intraday_volume")]:
        if target not in out.columns:
            out[target] = out[source] if source in out.columns else pd.NA
        else:
            out[target] = out[target].fillna(out[source] if source in out.columns else pd.NA)
    if metadata is not None and not metadata.empty and "market_cap" not in out.columns:
        out = out.merge(metadata[["ticker", "market_cap"]], on="ticker", how="left")
    elif metadata is not None and not metadata.empty:
        meta = metadata[["ticker", "market_cap"]].rename(columns={"market_cap": "metadata_market_cap"})
        out = out.merge(meta, on="ticker", how="left")
        out["market_cap"] = out["market_cap"].fillna(out["metadata_market_cap"])
        out = out.drop(columns=["metadata_market_cap"])
    if "market_cap" not in out.columns:
        out["market_cap"] = pd.NA
    return out


def apply_trade_quality_gate(
    signals: pd.DataFrame,
    config: AlpacaConfig,
    price_snapshot: Optional[pd.DataFrame] = None,
    metadata: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    if signals.empty or "ticker" not in signals.columns:
        return pd.DataFrame()
    tickers = signals["ticker"].astype(str).str.upper().dropna().unique().tolist()
    if price_snapshot is None and not _has_inline_market_context(signals):
        price_snapshot = latest_price_snapshot(tickers)
    if metadata is None:
        metadata = latest_metadata_snapshot()
    out = _prepare_market_context(signals, price_snapshot, metadata)
    out["current_price"] = pd.to_numeric(out["current_price"], errors="coerce")
    out["open_price"] = pd.to_numeric(out["open_price"], errors="coerce")
    out["intraday_high"] = pd.to_numeric(out["intraday_high"], errors="coerce")
    out["intraday_low"] = pd.to_numeric(out["intraday_low"], errors="coerce")
    out["intraday_volume"] = pd.to_numeric(out["intraday_volume"], errors="coerce").fillna(0)
    range_width = out["intraday_high"] - out["intraday_low"]
    out["price_position_in_intraday_range"] = ((out["current_price"] - out["intraday_low"]) / range_width.replace(0, pd.NA)).clip(0, 1)
    out["intraday_return_from_open"] = out["current_price"] / out["open_price"] - 1
    out["volatility_tier"] = out.apply(volatility_tier, axis=1)
    out["liquidity_tier"] = out.apply(liquidity_tier, axis=1)
    out["risk_tier"] = out.apply(risk_tier, axis=1)

    rows = []
    for _, row in out.iterrows():
        reasons = reject_reasons(row, config)
        if row["risk_tier"] == "reject":
            reasons.append("risk_tier_reject")
        side = "sell" if str(row.get("trade_action", "")).lower() == "short" else "buy"
        notional = approved_notional(config.max_notional_per_order, row["risk_tier"]) if not reasons else 0.0
        quantity = suggested_quantity(notional, numeric(row.get("current_price"), default=0))
        stop = {"stop_loss_price": pd.NA, "take_profit_price": pd.NA, "max_holding_days": pd.NA}
        try:
            if numeric(row.get("current_price"), default=0) > 0:
                stop = stop_take_profit_prices(float(row["current_price"]), side, str(row["volatility_tier"]))
        except Exception:
            reasons.append("stop_loss_cannot_be_calculated")
        if notional > 0 and quantity <= 0:
            reasons.append("quantity_below_one_share")
        row = row.to_dict()
        row.update(
            {
                "risk_tier": row.get("risk_tier", "reject"),
                "approved_notional": notional if not reasons else 0.0,
                "suggested_quantity": quantity if not reasons else 0,
                **stop,
                "trade_quality_status": "approved" if not reasons else "rejected",
                "trade_quality_reason": "approved" if not reasons else "|".join(dict.fromkeys(reasons)),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
