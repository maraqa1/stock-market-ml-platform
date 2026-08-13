from __future__ import annotations

from pathlib import Path

import pandas as pd

from stockml.candidates.execution_ranker import latest_execution_ranked_path
from stockml.common.paths import data_root, timestamp


AI2_INPUT_COLUMNS = [
    "raw_rank",
    "execution_rank",
    "symbol",
    "final_execution_side",
    "side",
    "status",
    "executable",
    "execution_domain",
    "order_eligible",
    "order_ready",
    "approved_notional",
    "suggested_quantity",
    "risk_tier",
    "volatility_tier",
    "validated_expected_return_bps",
    "net_expected_return_bps",
    "validated_hit_rate",
    "validated_profit_factor",
    "primary_block_reason",
    "all_block_reasons",
    "current_price",
    "limit_price",
    "close_price",
    "latest_intraday_price",
    "active_session_mode",
    "regular_session_eligible",
    "overnight_24_5_eligible",
    "tradable_session_set",
    "session_reject_reason",
    "estimated_execution_cost_bps",
    "validation_quality",
    "sizing_probability_source",
    "direction_gate_status",
    "direction_confidence",
    "ticker_direction_bias",
    "ticker_direction_sample_count",
    "pipeline_run_id",
    "strategy_version",
    "cycle_id",
    "signal_id",
    "candidate_id",
    "event_key",
    "lineage_source_path",
]


def _first_available_price(out: pd.DataFrame) -> pd.Series:
    price = pd.Series("", index=out.index, dtype="object")
    for column in ("current_price", "limit_price"):
        if column in out.columns:
            candidate = out[column]
            has_price = price.astype(str).str.strip().str.lower().isin({"", "nan", "none", "<na>"})
            price = price.where(~has_price, candidate)
    return price


def _fill_alias(out: pd.DataFrame, column: str, fallback: pd.Series) -> None:
    if column not in out.columns:
        out[column] = fallback
        return
    missing = out[column].astype(str).str.strip().str.lower().isin({"", "nan", "none", "<na>"})
    out[column] = out[column].where(~missing, fallback)


def build_ai2_candidate_input(candidates: pd.DataFrame, *, limit: int = 300) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=AI2_INPUT_COLUMNS)
    out = candidates.copy()
    if "symbol" not in out.columns:
        raise ValueError("candidate frame must include symbol")
    out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    price = _first_available_price(out)
    _fill_alias(out, "close_price", price)
    _fill_alias(out, "latest_intraday_price", price)
    for column in AI2_INPUT_COLUMNS:
        if column not in out.columns:
            out[column] = ""
    out["__rank"] = pd.to_numeric(out.get("execution_rank"), errors="coerce")
    out["__raw"] = pd.to_numeric(out.get("raw_rank"), errors="coerce").fillna(999999)
    out = out.sort_values(["__rank", "__raw", "symbol"], na_position="last", kind="mergesort").head(limit)
    return out[AI2_INPUT_COLUMNS].copy()


def write_ai2_candidate_input(
    candidates: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    limit: int = 300,
    stamp: str | None = None,
) -> Path:
    out_dir = Path(output_dir) if output_dir else data_root() / "ai2"
    out_dir.mkdir(parents=True, exist_ok=True)
    frame = build_ai2_candidate_input(candidates, limit=limit)
    path = out_dir / f"ai2_candidate_input_{stamp or timestamp()}.csv"
    frame.to_csv(path, index=False)
    return path


def write_latest_ai2_candidate_input(
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    limit: int = 300,
    stamp: str | None = None,
) -> tuple[Path | None, Path | None, int]:
    source = latest_execution_ranked_path(root)
    if source is None or not source.exists():
        return None, None, 0
    frame = pd.read_csv(source, low_memory=False)
    path = write_ai2_candidate_input(frame, output_dir=output_dir or data_root(root) / "ai2", limit=limit, stamp=stamp)
    return source, path, int(len(build_ai2_candidate_input(frame, limit=limit)))
