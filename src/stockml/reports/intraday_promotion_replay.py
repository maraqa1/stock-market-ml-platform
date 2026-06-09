from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.engine import Engine

from stockml.common.paths import MODEL_OUTPUTS_DIR, timestamp
from stockml.db.connection import get_engine
from stockml.db.schema import intraday_candidate_snapshots, intraday_promotion_log
from stockml.diagnostics.common import OUTCOME_COLUMNS, latest_gold, normalize_outcome_columns, norm_symbol_column


PROMOTION_VERDICTS = {"promote_to_selection", "promote_to_selection_strong"}


@dataclass(frozen=True)
class PromotionReplayOutputs:
    replay_path: Path
    summary_path: Path
    markdown_path: Path
    replay_rows: int
    summary_rows: int
    missing_inputs: tuple[str, ...] = ()


def build_intraday_promotion_replay(
    *,
    engine: Engine | None = None,
    gold_file: Path | None = None,
    output_dir: Path = MODEL_OUTPUTS_DIR,
    stamp: str | None = None,
    now: datetime | None = None,
) -> PromotionReplayOutputs:
    out_stamp = stamp or timestamp()
    replay_path = output_dir / f"intraday_promotion_replay_{out_stamp}.csv"
    summary_path = output_dir / f"intraday_promotion_false_diagnostics_{out_stamp}.csv"
    markdown_path = output_dir / f"intraday_promotion_replay_summary_{out_stamp}.md"
    output_dir.mkdir(parents=True, exist_ok=True)

    missing: list[str] = []
    rows = promotion_rows(engine=engine)
    if rows.empty:
        missing.append("intraday_promotion_log")
    gold_path = gold_file or latest_gold()
    gold = outcome_slice(gold_path, rows)
    if gold.empty:
        missing.append("gold_forward_outcomes")

    replay = attach_outcomes(rows, gold)
    if replay.empty and missing:
        replay = pd.DataFrame([{"status": "missing_data", "missing_inputs": "|".join(missing)}])
    summary = summarize_replay(replay)
    if summary.empty and missing:
        summary = pd.DataFrame([{"status": "missing_data", "missing_inputs": "|".join(missing), "count": 0}])

    replay.to_csv(replay_path, index=False)
    summary.to_csv(summary_path, index=False)
    markdown_path.write_text(render_summary(summary, missing=missing, now=now), encoding="utf-8")
    return PromotionReplayOutputs(
        replay_path=replay_path,
        summary_path=summary_path,
        markdown_path=markdown_path,
        replay_rows=len(replay),
        summary_rows=len(summary),
        missing_inputs=tuple(missing),
    )


def promotion_rows(*, engine: Engine | None = None) -> pd.DataFrame:
    db = engine or get_engine(required=True)
    joined = intraday_promotion_log.join(
        intraday_candidate_snapshots,
        intraday_promotion_log.c.snapshot_id == intraday_candidate_snapshots.c.id,
    )
    with db.connect() as conn:
        rows = conn.execute(
            select(
                intraday_promotion_log.c.id.label("promotion_id"),
                intraday_promotion_log.c.logged_at,
                intraday_promotion_log.c.snapshot_id,
                intraday_promotion_log.c.symbol,
                intraday_promotion_log.c.verdict,
                intraday_promotion_log.c.block_reason,
                intraday_promotion_log.c.nightly_score,
                intraday_promotion_log.c.intraday_adjustment,
                intraday_promotion_log.c.promotion_score,
                intraday_promotion_log.c.contributing,
                intraday_candidate_snapshots.c.snapshot_at,
                intraday_candidate_snapshots.c.bar_close_at,
                intraday_candidate_snapshots.c.nightly_bias,
                intraday_candidate_snapshots.c.spread_bps,
                intraday_candidate_snapshots.c.dollar_volume_today,
                intraday_candidate_snapshots.c.trend_5m_pct,
                intraday_candidate_snapshots.c.trend_15m_pct,
                intraday_candidate_snapshots.c.trend_30m_pct,
                intraday_candidate_snapshots.c.distance_from_vwap_bps,
                intraday_candidate_snapshots.c.intraday_range_position,
                intraday_candidate_snapshots.c.volatility_burst,
                intraday_candidate_snapshots.c.sector_etf_trend_5m_pct,
                intraday_candidate_snapshots.c.market_aligned,
            ).select_from(joined)
        ).mappings().all()
    return pd.DataFrame([dict(row) for row in rows])


def outcome_slice(gold_path: Path | None, replay: pd.DataFrame, *, chunksize: int = 250_000) -> pd.DataFrame:
    if gold_path is None or not gold_path.exists() or gold_path.stat().st_size == 0 or replay.empty:
        return pd.DataFrame()
    needed = replay.copy()
    needed["symbol"] = needed["symbol"].astype(str).str.upper().str.strip()
    needed["date"] = pd.to_datetime(needed["bar_close_at"], errors="coerce", utc=True).dt.date.astype(str)
    symbols = {symbol for symbol in needed["symbol"].dropna().astype(str) if symbol}
    dates = {date for date in needed["date"].dropna().astype(str) if date and date != "NaT"}
    if not symbols or not dates:
        return pd.DataFrame()
    chunks: list[pd.DataFrame] = []
    try:
        iterator = pd.read_csv(gold_path, usecols=lambda col: col in OUTCOME_COLUMNS, chunksize=chunksize, low_memory=False)
        for chunk in iterator:
            chunk = normalize_outcome_columns(chunk)
            if not {"ticker", "date"}.issubset(chunk.columns):
                continue
            chunk["ticker"] = chunk["ticker"].astype(str).str.upper().str.strip()
            chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce").dt.date.astype(str)
            selected = chunk[chunk["ticker"].isin(symbols) & chunk["date"].isin(dates)].copy()
            if not selected.empty:
                chunks.append(selected)
    except (pd.errors.EmptyDataError, ValueError):
        return pd.DataFrame()
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True).drop_duplicates(["ticker", "date"], keep="last")


def attach_outcomes(replay: pd.DataFrame, gold: pd.DataFrame) -> pd.DataFrame:
    if replay.empty:
        return replay
    out = replay.copy()
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    out["decision_date"] = pd.to_datetime(out["bar_close_at"], errors="coerce", utc=True).dt.date.astype(str)
    for column in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"]:
        if column not in out.columns:
            out[column] = pd.NA
    if not gold.empty:
        gold = norm_symbol_column(gold)
        if {"ticker", "date"}.issubset(gold.columns):
            keep = [col for col in ["ticker", "date", "forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"] if col in gold.columns]
            lookup = gold[keep].copy()
            lookup["date"] = pd.to_datetime(lookup["date"], errors="coerce").dt.date.astype(str)
            out = out.merge(
                lookup.drop_duplicates(["ticker", "date"], keep="last"),
                left_on=["symbol", "decision_date"],
                right_on=["ticker", "date"],
                how="left",
                suffixes=("", "_gold"),
            )
            for column in ["forward_5d_return", "forward_5d_alpha_vs_spy", "forward_5d_alpha_vs_sector", "sector"]:
                gold_column = f"{column}_gold"
                if gold_column in out.columns:
                    out[column] = out[column].fillna(out[gold_column])
            out = out.drop(columns=[col for col in ["ticker", "date", "forward_5d_return_gold", "forward_5d_alpha_vs_spy_gold", "forward_5d_alpha_vs_sector_gold", "sector_gold"] if col in out.columns])
    return classify_false_promotions(out)


def classify_false_promotions(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    promoted = out["verdict"].astype(str).isin(PROMOTION_VERDICTS)
    side = out["nightly_bias"].astype(str).str.lower().map({"long": 1.0, "short": -1.0}).fillna(0.0)
    forward = pd.to_numeric(out["forward_5d_return"], errors="coerce")
    sector_alpha = pd.to_numeric(out["forward_5d_alpha_vs_sector"], errors="coerce")
    out["directional_forward_5d_bps"] = forward * side * 10000.0
    out["directional_sector_alpha_5d_bps"] = sector_alpha * side * 10000.0
    out["false_promotion"] = False
    out["false_promotion_reason"] = ""
    missing_outcome = promoted & out["directional_forward_5d_bps"].isna()
    losing = promoted & out["directional_forward_5d_bps"].notna() & (out["directional_forward_5d_bps"] <= 0)
    sector_lag = promoted & out["directional_sector_alpha_5d_bps"].notna() & (out["directional_sector_alpha_5d_bps"] <= 0)
    out.loc[losing | sector_lag, "false_promotion"] = True
    out.loc[missing_outcome, "false_promotion_reason"] = "missing_forward_outcome"
    out.loc[losing, "false_promotion_reason"] = "negative_directional_forward_return"
    out.loc[sector_lag & ~losing, "false_promotion_reason"] = "negative_directional_sector_alpha"
    out.loc[~promoted, "false_promotion_reason"] = "not_promoted"
    out["promotion_replay_status"] = "observed"
    out.loc[missing_outcome, "promotion_replay_status"] = "missing_outcome"
    return out


def summarize_replay(replay: pd.DataFrame) -> pd.DataFrame:
    if replay.empty or "verdict" not in replay.columns:
        return pd.DataFrame()
    group_cols = ["verdict", "nightly_bias", "false_promotion_reason"]
    rows: list[dict[str, Any]] = []
    for keys, group in replay.groupby(group_cols, dropna=False):
        verdict, bias, reason = keys
        promoted = group["verdict"].astype(str).isin(PROMOTION_VERDICTS)
        false_rate = float(group.loc[promoted, "false_promotion"].mean()) if promoted.any() else 0.0
        rows.append(
            {
                "verdict": verdict,
                "nightly_bias": bias,
                "false_promotion_reason": reason,
                "count": len(group),
                "promoted_count": int(promoted.sum()),
                "false_promotion_count": int(group["false_promotion"].sum()) if "false_promotion" in group else 0,
                "false_promotion_rate": false_rate,
                "mean_promotion_score": _mean(group, "promotion_score"),
                "mean_intraday_adjustment": _mean(group, "intraday_adjustment"),
                "mean_directional_forward_5d_bps": _mean(group, "directional_forward_5d_bps"),
                "mean_directional_sector_alpha_5d_bps": _mean(group, "directional_sector_alpha_5d_bps"),
                "mean_spread_bps": _mean(group, "spread_bps"),
                "mean_dollar_volume_today": _mean(group, "dollar_volume_today"),
            }
        )
    return pd.DataFrame(rows).sort_values(["verdict", "nightly_bias", "false_promotion_reason"]).reset_index(drop=True)


def render_summary(summary: pd.DataFrame, *, missing: list[str], now: datetime | None = None) -> str:
    stamp = now or datetime.now(timezone.utc)
    lines = ["# Intraday Promotion Replay", "", f"- generated_at: {stamp.isoformat()}"]
    if missing:
        lines.append(f"- missing inputs: {', '.join(missing)}")
    if not summary.empty and {"promoted_count", "false_promotion_count"}.issubset(summary.columns):
        promoted = int(pd.to_numeric(summary["promoted_count"], errors="coerce").fillna(0).sum())
        false_count = int(pd.to_numeric(summary["false_promotion_count"], errors="coerce").fillna(0).sum())
        rate = false_count / promoted if promoted else 0.0
        lines.extend(["", "## Totals", f"- promoted_count: {promoted}", f"- false_promotion_count: {false_count}", f"- false_promotion_rate: {rate:.4f}"])
    lines.extend(["", "## Notes", "- This report is read-only and does not change trading thresholds, gates, scoring, or exposure."])
    return "\n".join(lines) + "\n"


def _mean(frame: pd.DataFrame, column: str) -> float:
    if column not in frame.columns:
        return float("nan")
    return float(pd.to_numeric(frame[column], errors="coerce").mean())
