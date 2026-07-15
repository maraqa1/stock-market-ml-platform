from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.common.paths import GOLD_DIR, INTERIM_DIR, PROCESSED_DIR, RAW_DIR, latest_file
from stockml.diagnostics.validation_bucket_calibration import map_candidates_to_calibration
from stockml.trading.config import AlpacaConfig
from stockml.trading.position_sizing import approved_notional, base_notional, suggested_quantity
from stockml.trading.risk_checks import liquidity_tier, numeric, reject_reasons, risk_tier, volatility_tier
from stockml.trading.spread_edge import evaluate_spread_edge, expected_move_bps_from
from stockml.trading.stop_take_profit import stop_take_profit_prices
from stockml.trading.volatility_opportunity import evaluate_volatility_opportunity


PRICE_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
RISK_FEATURE_COLUMNS = ["date", "ticker", "avg_dollar_volume_20d", "volatility_20d", "market_cap", "sector"]
SOURCE_MARKET_COLUMNS = ["close", "open", "high", "low", "volume"]
QUALITY_MARKET_COLUMNS = ["current_price", "open_price", "intraday_high", "intraday_low", "intraday_volume"]
ROUND_UP_RISK_TIERS = {"high_quality", "medium", "large_liquid", "mid_risk"}


def _has_inline_market_context(signals: pd.DataFrame) -> bool:
    source_columns_present = all(column in signals.columns for column in SOURCE_MARKET_COLUMNS)
    quality_columns_present = all(column in signals.columns for column in QUALITY_MARKET_COLUMNS)
    return source_columns_present or quality_columns_present


def _numeric_column(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def latest_expected_return_calibration() -> pd.DataFrame:
    path = PROCESSED_DIR.parent / "model_outputs" / "validation" / "expected_return_bucket_calibration_latest.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _apply_expected_return_calibration(signals: pd.DataFrame, calibration: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    if signals.empty:
        return signals
    source = calibration if calibration is not None else latest_expected_return_calibration()
    if source is None or source.empty:
        return signals
    mapped = map_candidates_to_calibration(signals, source)
    out = signals.copy()
    for column in [
        "calibrated_bucket_id",
        "validated_expected_return_bps",
        "validated_hit_rate",
        "validated_profit_factor",
        "calibration_source",
        "calibration_quality",
        "expected_return_quality",
    ]:
        out[column] = mapped[column].reindex(out.index)
    return out


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


def latest_metadata_snapshot(metadata_file: Optional[Path] = None, tickers: Optional[list[str]] = None) -> pd.DataFrame:
    path = metadata_file or latest_file(INTERIM_DIR, "04_us_metadata_enriched_*.csv")
    if path is None or not path.exists():
        return pd.DataFrame()
    cols = ["ticker", "market_cap"]
    wanted = {str(ticker).upper().strip() for ticker in tickers or [] if str(ticker).strip()}

    def read_one(item: Path) -> pd.DataFrame:
        try:
            frame = pd.read_csv(item, usecols=lambda col: col in cols, low_memory=False)
        except Exception:
            return pd.DataFrame()
        if "ticker" not in frame.columns:
            return pd.DataFrame()
        frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
        if wanted:
            frame = frame[frame["ticker"].isin(wanted)].copy()
        if "market_cap" not in frame.columns:
            frame["market_cap"] = pd.NA
        frame["market_cap"] = pd.to_numeric(frame["market_cap"], errors="coerce")
        return frame.dropna(subset=["market_cap"]).drop_duplicates("ticker", keep="last")

    if metadata_file is not None:
        return read_one(path)

    files = sorted(INTERIM_DIR.glob("04_us_metadata_enriched_*.csv"), key=lambda item: item.stat().st_mtime, reverse=True)
    rows = []
    seen: set[str] = set()
    for item in files[:8]:
        frame = read_one(item)
        if frame.empty:
            continue
        if seen:
            frame = frame[~frame["ticker"].isin(seen)].copy()
        if frame.empty:
            continue
        rows.append(frame)
        seen.update(frame["ticker"].tolist())
        if wanted and wanted.issubset(seen):
            break
    if rows:
        return pd.concat(rows, ignore_index=True).drop_duplicates("ticker", keep="first")
    try:
        frame = pd.read_csv(path, usecols=lambda col: col in cols, low_memory=False)
    except Exception:
        return pd.DataFrame()
    frame["ticker"] = frame["ticker"].astype(str).str.upper().str.strip()
    return frame.drop_duplicates("ticker", keep="last")


def latest_risk_feature_snapshot(tickers: list[str], feature_file: Optional[Path] = None) -> pd.DataFrame:
    path = feature_file or latest_file(GOLD_DIR, "06_us_gold_ml_dataset_*.csv") or latest_file(PROCESSED_DIR, "05_us_feature_panel_*.csv")
    if path is None or not path.exists() or not tickers:
        return pd.DataFrame()
    wanted = {str(ticker).upper() for ticker in tickers}
    rows = []
    try:
        for chunk in pd.read_csv(path, usecols=lambda col: col in RISK_FEATURE_COLUMNS, chunksize=200_000, low_memory=False):
            chunk["ticker"] = chunk["ticker"].astype(str).str.upper().str.strip()
            subset = chunk[chunk["ticker"].isin(wanted)]
            if not subset.empty:
                rows.append(subset)
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    features = pd.concat(rows, ignore_index=True)
    features["date"] = pd.to_datetime(features["date"], errors="coerce")
    return features.sort_values(["ticker", "date"]).groupby("ticker", as_index=False).tail(1)


def _prepare_market_context(
    signals: pd.DataFrame,
    price_snapshot: Optional[pd.DataFrame],
    metadata: Optional[pd.DataFrame],
    risk_features: Optional[pd.DataFrame] = None,
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
    if "avg_dollar_volume_20d" not in out.columns:
        out["avg_dollar_volume_20d"] = out["current_price"].apply(lambda _: pd.NA)
    if "volatility_20d" not in out.columns:
        out["volatility_20d"] = out["current_price"].apply(lambda _: pd.NA)
    if risk_features is not None and not risk_features.empty:
        features = risk_features.copy()
        features["ticker"] = features["ticker"].astype(str).str.upper().str.strip()
        rename = {
            "avg_dollar_volume_20d": "feature_avg_dollar_volume_20d",
            "volatility_20d": "feature_volatility_20d",
            "market_cap": "feature_market_cap",
            "sector": "feature_sector",
        }
        features = features.rename(columns=rename)
        keep = ["ticker", *[col for col in rename.values() if col in features.columns]]
        out = out.merge(features[keep], on="ticker", how="left")
        for source, target in [
            ("feature_avg_dollar_volume_20d", "avg_dollar_volume_20d"),
            ("feature_volatility_20d", "volatility_20d"),
            ("feature_market_cap", "market_cap"),
            ("feature_sector", "sector"),
        ]:
            if source in out.columns and target in out.columns:
                out[target] = out[target].fillna(out[source])
                out = out.drop(columns=[source])
    return out


def _directional_round_up_allowed(row: pd.Series, config: AlpacaConfig, price: float) -> bool:
    if not bool(getattr(config, "directional_round_up_enabled", True)):
        return False
    if price <= 0:
        return False
    action = str(row.get("trade_action", "")).strip().lower()
    directional_action = str(row.get("directional_action", "")).strip().lower()
    if action not in {"long", "short"} or directional_action != action:
        return False
    if str(row.get("risk_tier", "")).strip().lower() not in ROUND_UP_RISK_TIERS:
        return False
    if str(row.get("liquidity_tier", "")).strip().lower() not in {"high", "medium"}:
        return False
    strength = numeric(row.get("directional_strength"), default=0)
    if strength < float(getattr(config, "directional_round_up_min_strength", 0.97)):
        return False
    max_notional = float(config.account_equity) * float(getattr(config, "directional_round_up_max_equity_pct", 0.05))
    return price <= max_notional


def apply_trade_quality_gate(
    signals: pd.DataFrame,
    config: AlpacaConfig,
    price_snapshot: Optional[pd.DataFrame] = None,
    metadata: Optional[pd.DataFrame] = None,
    risk_features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    if signals.empty or "ticker" not in signals.columns:
        return pd.DataFrame()
    tickers = signals["ticker"].astype(str).str.upper().dropna().unique().tolist()
    if price_snapshot is None and not _has_inline_market_context(signals):
        price_snapshot = latest_price_snapshot(tickers)
    if metadata is None:
        metadata = latest_metadata_snapshot(tickers=tickers)
    if risk_features is None:
        risk_features = latest_risk_feature_snapshot(tickers)
    out = _prepare_market_context(signals, price_snapshot, metadata, risk_features)
    out = _apply_expected_return_calibration(out)
    out["current_price"] = pd.to_numeric(out["current_price"], errors="coerce")
    out["open_price"] = pd.to_numeric(out["open_price"], errors="coerce")
    out["intraday_high"] = pd.to_numeric(out["intraday_high"], errors="coerce")
    out["intraday_low"] = pd.to_numeric(out["intraday_low"], errors="coerce")
    out["intraday_volume"] = pd.to_numeric(out["intraday_volume"], errors="coerce").fillna(0)
    out["market_cap"] = pd.to_numeric(out["market_cap"], errors="coerce")
    out["avg_dollar_volume_20d"] = pd.to_numeric(out["avg_dollar_volume_20d"], errors="coerce")
    out["expected_trade_return"] = _numeric_column(out, "expected_trade_return")
    out["risk_adjusted_score"] = _numeric_column(out, "risk_adjusted_score")
    out["side_probability"] = _numeric_column(out, "side_probability")
    if "spread_bps" in out.columns:
        out["spread_bps"] = pd.to_numeric(out["spread_bps"], errors="coerce")
    range_width = out["intraday_high"] - out["intraday_low"]
    out["price_position_in_intraday_range"] = ((out["current_price"] - out["intraday_low"]) / range_width.replace(0, pd.NA)).clip(0, 1)
    out["intraday_return_from_open"] = out["current_price"] / out["open_price"] - 1
    out["volatility_tier"] = out.apply(volatility_tier, axis=1)
    out["liquidity_tier"] = out.apply(liquidity_tier, axis=1)
    out["risk_tier"] = out.apply(risk_tier, axis=1)

    rows = []
    base = base_notional(config.account_equity, config.max_position_pct, config.max_total_notional, config.max_orders)
    for _, row in out.iterrows():
        reasons = reject_reasons(row, config)
        volatility_opportunity = evaluate_volatility_opportunity(row, reasons)
        if volatility_opportunity["volatility_opportunity_allows_reduced_trade"]:
            reasons = [reason for reason in reasons if reason != "volatility_extreme"]
            row["risk_tier"] = "speculative"
        side = "sell" if str(row.get("trade_action", "")).lower() == "short" else "buy"
        hard_reject = bool(reasons) or row["risk_tier"] == "reject"
        notional = approved_notional(base, row["risk_tier"], numeric(row.get("side_probability"), default=0)) if not hard_reject else 0.0
        current_price = numeric(row.get("current_price"), default=0)
        quantity = suggested_quantity(notional, current_price)
        sizing_reason = "standard_floor_quantity"
        stop = {"stop_loss_price": pd.NA, "take_profit_price": pd.NA, "max_holding_days": pd.NA}
        spread_value = pd.to_numeric(row.get("spread_bps"), errors="coerce") if "spread_bps" in out.columns else pd.NA
        spread_edge = evaluate_spread_edge(
            spread_bps=None if pd.isna(spread_value) else float(spread_value),
            max_spread_bps=25.0,
            expected_move_bps=expected_move_bps_from(row),
            estimated_cost_bps=float(getattr(config, "transaction_cost_bps", 10.0)),
        )
        try:
            if current_price > 0:
                stop = stop_take_profit_prices(float(row["current_price"]), side, str(row["volatility_tier"]), str(row["risk_tier"]))
        except Exception:
            reasons.extend(["stop_loss_unavailable", "take_profit_unavailable"])
            hard_reject = True
        if notional > 0 and quantity <= 0:
            if not hard_reject and _directional_round_up_allowed(row, config, current_price):
                quantity = 1
                notional = round(current_price, 2)
                sizing_reason = "directional_one_share_round_up"
            else:
                reasons.append("quantity_below_one")
                hard_reject = True
        status = "rejected"
        if not hard_reject and not reasons:
            status = "approved" if row["risk_tier"] == "high_quality" else "reduced"
        row = row.to_dict()
        row.update(
            {
                "risk_tier": row.get("risk_tier", "reject"),
                "approved_notional": notional if status in {"approved", "reduced"} else 0.0,
                "suggested_quantity": quantity if status in {"approved", "reduced"} else 0,
                "position_sizing_reason": sizing_reason if status in {"approved", "reduced"} else "rejected",
                **spread_edge.details(),
                **stop,
                **volatility_opportunity,
                "trade_quality_status": status,
                "trade_quality_reason": status if status in {"approved", "reduced"} else "|".join(dict.fromkeys(reasons or ["risk_tier_reject"])),
                "order_eligible": bool(status in {"approved", "reduced"} and quantity >= 1),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
