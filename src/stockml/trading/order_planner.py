from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from stockml.decisions.meta_label_gate import MetaLabelGateConfig, apply_meta_label_gate
from stockml.models.meta_labeling import load_meta_label_config
from stockml.common.paths import MODEL_OUTPUTS_DIR, latest_file
from stockml.trading.config import AlpacaConfig
from stockml.trading.order_builder import extended_limit_price, order_row
from stockml.trading.position_sizing import apply_same_day_sizing
from stockml.trading.trade_quality_gate import apply_trade_quality_gate


REQUIRED_SIGNAL_COLUMNS = {
    "ticker",
    "trade_action",
}


def latest_signal_table(path: Optional[Path] = None) -> pd.DataFrame:
    signal_file = path or latest_file(MODEL_OUTPUTS_DIR, "advanced_model_signal_table_*.csv")
    if signal_file is None or not signal_file.exists():
        return pd.DataFrame()
    return pd.read_csv(signal_file, low_memory=False)


def _valid_action(value: object) -> bool:
    return str(value or "").strip().lower() in {"long", "short"}


def _side(action: str) -> str:
    return "buy" if action.lower() == "long" else "sell"


def _notional_order(row: pd.Series, config: AlpacaConfig) -> dict:
    action = str(row["trade_action"])
    side = _side(action)
    extended = bool(config.extended_hours or config.overnight_trading_enabled)
    entry_type = "limit" if extended else "market"
    limit_price = extended_limit_price(row, side, config.overnight_limit_buffer_bps) if extended else None
    return {
        "symbol": str(row["ticker"]).upper(),
        "notional": round(float(config.max_notional_per_order), 2),
        "side": side,
        "type": entry_type,
        "time_in_force": "day",
        "extended_hours": extended,
        "limit_price": limit_price if limit_price is not None else "",
        "client_order_id": f"stockml-{str(row.get('date', 'latest')).replace('-', '')}-{str(row['ticker']).upper()}-{side}",
    }


def _numeric_column(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _eligible_order_mask(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(False, index=frame.index, dtype="bool")
    return (
        frame["trade_quality_status"].astype(str).str.lower().isin({"approved", "reduced"})
        & frame["order_eligible"].astype(bool)
        & (pd.to_numeric(frame["suggested_quantity"], errors="coerce").fillna(0) >= 1)
    )


def _add_final_selection_sort(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["_sort_score"] = pd.to_numeric(out.get("risk_adjusted_score", 0), errors="coerce").abs().fillna(0)
    out["_directional_strength_sort"] = pd.to_numeric(out.get("directional_strength", 0), errors="coerce").fillna(0)
    out["_eligible_sort"] = _eligible_order_mask(out).astype(int)
    out["_quality_sort"] = out.get("trade_quality_status", pd.Series("", index=out.index)).astype(str).str.lower().map({"approved": 2, "reduced": 1}).fillna(0)
    if "candidate_rank" in out.columns:
        out["_candidate_rank_sort"] = pd.to_numeric(out["candidate_rank"], errors="coerce").fillna(999_999)
    else:
        out["_candidate_rank_sort"] = range(1, len(out) + 1)
    return out


def _drop_selection_sort(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.drop(columns=["_sort_score", "_directional_strength_sort", "_eligible_sort", "_quality_sort", "_candidate_rank_sort"], errors="ignore")


def _sort_final_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(
        ["_eligible_sort", "_directional_strength_sort", "_quality_sort", "_sort_score", "_candidate_rank_sort"],
        ascending=[False, False, False, False, True],
    )


def _balanced_side_selection(frame: pd.DataFrame, config: AlpacaConfig, limit: int) -> pd.DataFrame:
    if frame.empty or not config.allow_short_selling:
        return frame.head(limit)
    actions = frame["trade_action"].astype(str).str.strip().str.lower()
    longs = frame[actions.eq("long")].copy()
    shorts = frame[actions.eq("short")].copy()
    if longs.empty or shorts.empty:
        return frame.head(limit)

    long_slots = (limit + 1) // 2
    short_slots = limit // 2
    selected = pd.concat([longs.head(long_slots), shorts.head(short_slots)], ignore_index=False)
    if len(selected) < limit:
        remaining = frame.drop(index=selected.index, errors="ignore")
        selected = pd.concat([selected, remaining.head(limit - len(selected))], ignore_index=False)
    return selected.sort_values("_sort_score", ascending=False).head(limit)


def filter_tradeable_signals(signals: pd.DataFrame, config: AlpacaConfig, limit: int | None = None) -> pd.DataFrame:
    if signals.empty or not REQUIRED_SIGNAL_COLUMNS.issubset(signals.columns):
        return pd.DataFrame()
    limit = limit or config.max_orders
    frame = signals.copy()
    actions = frame["trade_action"].astype(str).str.strip().str.lower()
    allowed_actions = {"long"}
    if config.allow_short_selling:
        allowed_actions.add("short")
    frame = frame[actions.isin(allowed_actions)].copy()
    if "model_status" in frame.columns:
        frame = frame[frame["model_status"].astype(str).str.strip().str.lower().ne("diagnostic_only")].copy()
    if "decision_grade" in frame.columns:
        frame = frame[frame["decision_grade"].astype(str).str.strip().str.lower().ne("diagnostic_only")].copy()
    if "diagnostic_only" in frame.columns:
        diagnostic = frame["diagnostic_only"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
        diagnostic_reason = frame.get("signal_reason", pd.Series("", index=frame.index)).astype(str).str.contains("diagnostic_paper_candidate", case=False, na=False)
        frame = frame[(~diagnostic) | diagnostic_reason].copy()
    if frame.empty:
        return pd.DataFrame()
    frame["side_probability"] = _numeric_column(frame, "side_probability")
    frame["probability_edge"] = _numeric_column(frame, "probability_edge")
    if "close" in frame.columns:
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["risk_adjusted_score"] = _numeric_column(frame, "risk_adjusted_score")
    frame["_sort_score"] = frame["risk_adjusted_score"].abs()
    frame = frame.sort_values("_sort_score", ascending=False)
    frame = _balanced_side_selection(frame, config, limit)
    frame = _limit_sector_concentration(frame, config, limit)
    return frame.head(limit).drop(columns=["_sort_score"])


def _ranked_shortlist(signals: pd.DataFrame, config: AlpacaConfig) -> pd.DataFrame:
    if signals.empty or "ticker" not in signals.columns:
        return pd.DataFrame()
    if "rank_overall" not in signals.columns:
        return filter_tradeable_signals(signals, config, limit=max(config.candidate_pool_size, config.max_orders))

    frame = signals.copy()
    frame["rank_overall"] = pd.to_numeric(frame["rank_overall"], errors="coerce")
    frame = frame[frame["rank_overall"].notna()].copy()
    if frame.empty:
        return filter_tradeable_signals(signals, config, limit=max(config.candidate_pool_size, config.max_orders))

    size = max(config.candidate_pool_size, config.max_orders)
    if "directional_action" in frame.columns:
        directional = frame[frame["directional_action"].map(_valid_action)].copy()
        if not directional.empty:
            return _directional_shortlist(directional, config, size)

    long_slots = (size + 1) // 2
    short_slots = size // 2
    longs = frame.sort_values("rank_overall", ascending=True).head(long_slots).copy()
    shorts = frame.sort_values("rank_overall", ascending=False).head(short_slots).copy()
    longs["trade_action"] = "Long"
    shorts["trade_action"] = "Short"
    shortlist = pd.concat([longs, shorts], ignore_index=False)

    shortlist["side_probability"] = _numeric_column(shortlist, "side_probability")
    shortlist["probability_edge"] = _numeric_column(shortlist, "probability_edge")
    shortlist["risk_adjusted_score"] = _numeric_column(shortlist, "risk_adjusted_score")
    shortlist["_sort_score"] = shortlist["risk_adjusted_score"].abs()
    shortlist = shortlist.sort_values(["trade_action", "rank_overall"], ascending=[True, True])
    return shortlist.drop(columns=["_sort_score"], errors="ignore").head(size)


def _directional_shortlist(frame: pd.DataFrame, config: AlpacaConfig, size: int) -> pd.DataFrame:
    out = frame.copy()
    out["trade_action"] = out["directional_action"].astype(str).str.strip().str.title()
    out["directional_strength"] = _numeric_column(out, "directional_strength")
    out["side_probability"] = _numeric_column(out, "side_probability")
    out["probability_edge"] = _numeric_column(out, "probability_edge")
    out["risk_adjusted_score"] = _numeric_column(out, "risk_adjusted_score")
    out["_sort_score"] = (
        out["directional_strength"].mul(100.0)
        + out["side_probability"].fillna(0.0)
        + out["risk_adjusted_score"].abs().fillna(0.0)
    )

    actions = out["trade_action"].astype(str).str.strip().str.lower()
    longs = out[actions.eq("long")].sort_values(["_sort_score", "rank_overall"], ascending=[False, True]).copy()
    shorts = out[actions.eq("short")].sort_values(["_sort_score", "rank_overall"], ascending=[False, False]).copy()

    long_fraction = min(max(float(getattr(config, "directional_candidate_long_fraction", 0.70)), 0.0), 1.0)
    long_slots = min(len(longs), max(0, int(round(size * long_fraction))))
    short_slots = min(len(shorts), max(0, size - long_slots))
    selected = pd.concat([longs.head(long_slots), shorts.head(short_slots)], ignore_index=False)
    if len(selected) < size:
        remaining = out.drop(index=selected.index, errors="ignore").sort_values("_sort_score", ascending=False)
        selected = pd.concat([selected, remaining.head(size - len(selected))], ignore_index=False)

    selected["signal_reason"] = selected.get("signal_reason", pd.Series("", index=selected.index)).fillna("").astype(str)
    directional_reason = selected.get("directional_reason", pd.Series("", index=selected.index)).fillna("").astype(str)
    selected.loc[selected["signal_reason"].eq(""), "signal_reason"] = directional_reason
    return selected.sort_values("_sort_score", ascending=False).drop(columns=["_sort_score"], errors="ignore").head(size)


def _limit_sector_concentration(frame: pd.DataFrame, config: AlpacaConfig, limit: int | None = None) -> pd.DataFrame:
    if "sector" not in frame.columns or frame.empty:
        return frame
    limit = limit or config.max_orders
    max_fraction = min(max(config.max_sector_fraction, 0.0), 1.0)
    if max_fraction <= 0:
        return frame.iloc[0:0]
    max_per_sector = max(1, int(limit * max_fraction))
    selected = []
    sector_counts: dict[str, int] = {}
    for _, row in frame.iterrows():
        sector = str(row.get("sector") or "Unknown")
        if sector_counts.get(sector, 0) >= max_per_sector:
            continue
        selected.append(row)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
        if len(selected) >= limit:
            break
    if not selected:
        return frame.iloc[0:0]
    return pd.DataFrame(selected)


def build_candidate_pool(
    signals: pd.DataFrame,
    config: AlpacaConfig,
    price_snapshot: Optional[pd.DataFrame] = None,
    metadata: Optional[pd.DataFrame] = None,
    open_positions: Optional[pd.DataFrame] = None,
    same_day_realized_pnl_today: float = 0.0,
) -> pd.DataFrame:
    filtered = _ranked_shortlist(signals, config)
    if filtered.empty:
        return pd.DataFrame()
    gated = apply_trade_quality_gate(filtered, config, price_snapshot=price_snapshot, metadata=metadata)
    if "meta_label_probability" in gated.columns:
        meta_cfg = load_meta_label_config()
        gated = apply_meta_label_gate(
            gated,
            MetaLabelGateConfig(
                enabled=meta_cfg.enabled,
                min_meta_label_probability=meta_cfg.min_meta_label_probability,
                transaction_cost_bps=meta_cfg.transaction_cost_bps,
            ),
        )
    pool = pd.DataFrame([order_row(row, config) for _, row in gated.iterrows()])
    if pool.empty:
        return pool
    pool["candidate_rank"] = range(1, len(pool) + 1)
    pool["candidate_status"] = pool["trade_quality_status"]
    pool = apply_same_day_sizing(
        pool,
        account_equity=config.account_equity,
        open_positions=open_positions,
        same_day_realized_pnl_today=same_day_realized_pnl_today,
    )
    return pool


def _select_final_orders(candidate_pool: pd.DataFrame, config: AlpacaConfig) -> pd.DataFrame:
    if candidate_pool.empty:
        return candidate_pool
    sortable = _add_final_selection_sort(candidate_pool)
    if not config.allow_short_selling:
        eligible = sortable[sortable["_eligible_sort"].eq(1)].copy()
        selected = _sort_final_candidates(eligible if not eligible.empty else sortable).head(config.max_orders)
        selected = _drop_selection_sort(selected)
        selected = _limit_sector_concentration(selected, config, config.max_orders)
        return selected.head(config.max_orders).copy()

    actions = sortable["trade_action"].astype(str).str.strip().str.lower()
    longs = _sort_final_candidates(sortable[actions.eq("long")].copy())
    shorts = _sort_final_candidates(sortable[actions.eq("short")].copy())
    if longs.empty or shorts.empty:
        selected = _sort_final_candidates(sortable).head(config.max_orders)
        selected = _drop_selection_sort(selected)
        selected = _limit_sector_concentration(selected, config, config.max_orders)
        return selected.head(config.max_orders).copy()

    long_slots = (config.max_orders + 1) // 2
    short_slots = config.max_orders // 2
    selected = pd.concat([longs.head(long_slots), shorts.head(short_slots)], ignore_index=False)
    if len(selected) < config.max_orders:
        remaining = sortable.drop(index=selected.index, errors="ignore")
        selected = pd.concat(
            [selected, _sort_final_candidates(remaining).head(config.max_orders - len(selected))],
            ignore_index=False,
        )
    selected = _drop_selection_sort(selected)
    selected = _limit_sector_concentration(selected, config, config.max_orders)
    return selected.head(config.max_orders).copy()


def build_order_plan(
    signals: pd.DataFrame,
    config: AlpacaConfig,
    price_snapshot: Optional[pd.DataFrame] = None,
    metadata: Optional[pd.DataFrame] = None,
    open_positions: Optional[pd.DataFrame] = None,
    same_day_realized_pnl_today: float = 0.0,
) -> pd.DataFrame:
    candidate_pool = build_candidate_pool(
        signals,
        config,
        price_snapshot=price_snapshot,
        metadata=metadata,
        open_positions=open_positions,
        same_day_realized_pnl_today=same_day_realized_pnl_today,
    )
    if candidate_pool.empty:
        return pd.DataFrame()
    gated = _select_final_orders(candidate_pool, config)
    running_notional = 0.0
    for idx, row in gated.iterrows():
        if str(row.get("trade_quality_status", "")).lower() not in {"approved", "reduced"}:
            continue
        approved = float(row.get("approved_notional") or 0)
        if running_notional + approved > config.max_total_notional:
            gated.loc[idx, "trade_quality_status"] = "rejected"
            gated.loc[idx, "trade_quality_reason"] = "max_basket_notional_reached"
            gated.loc[idx, "approved_notional"] = 0.0
            gated.loc[idx, "suggested_quantity"] = 0
            gated.loc[idx, "order_eligible"] = False
        else:
            running_notional += approved
    return gated.reset_index(drop=True)
