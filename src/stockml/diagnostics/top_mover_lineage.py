from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.diagnostics.source_direction_coverage import source_no_decision_reason


DIAGNOSTIC_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"
DATA_DIR = PROJECT_ROOT / "data"
STAGES = [
    "universe",
    "price_history",
    "validated_universe",
    "metadata",
    "feature_panel",
    "gold_v2",
    "model_signal",
    "source_trade_action",
    "candidate_pool",
    "execution_domain",
    "order_plan",
]


@dataclass(frozen=True)
class TopMoverLineageOutput:
    detail_path: Path
    summary_path: Path
    detail: pd.DataFrame


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


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


def _latest(pattern: str) -> Path | None:
    files = sorted(PROJECT_ROOT.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def latest_lineage_paths() -> dict[str, Path | None]:
    return {
        "universe": _latest("data/interim/02_us_tradable_universe_*.csv"),
        "price_history": _latest("data/interim/03_us_price_history_quality_*.csv"),
        "validated_universe": _latest("data/interim/03_us_price_validated_universe_*.csv"),
        "metadata": _latest("data/interim/04_us_metadata_enriched_*.csv"),
        "feature_panel": _latest("data/processed/05_us_feature_panel_*.csv"),
        "gold_v2": _latest("data/gold/gold_stock_decision_daily_*.csv") or _latest("data/gold/06_us_gold_ml_dataset_*.csv"),
        "model_signal": _latest("data/model_outputs/advanced_model_signal_table_*.csv"),
        "candidate_pool": _latest("data/portal_outputs/08_alpaca_paper_candidate_pool_*.csv"),
        "execution_ranked": _latest("data/portal_outputs/execution_ranked_candidates_*.csv"),
        "order_plan": _latest("data/portal_outputs/08_alpaca_paper_order_plan_*.csv"),
    }


def normalize_movers(symbols: list[str] | None = None, input_csv: Path | str | None = None) -> pd.DataFrame:
    if input_csv:
        frame = pd.read_csv(input_csv, low_memory=False)
    else:
        frame = pd.DataFrame({"symbol": symbols or []})
    if "symbol" not in frame.columns:
        raise ValueError("mover input requires a symbol column")
    out = frame.copy()
    out["requested_symbol"] = out["symbol"].fillna("").astype(str).str.upper().str.strip()
    out["normalized_symbol"] = out["requested_symbol"]
    for column in ["screenshot_direction", "screenshot_price", "mover_type", "source", "observed_at"]:
        if column not in out.columns:
            out[column] = ""
    return out


def _symbol_col(columns: list[str]) -> str | None:
    if "symbol" in columns:
        return "symbol"
    if "ticker" in columns:
        return "ticker"
    return None


def _slice_csv(path: Path | None, symbols: set[str], columns: set[str], *, chunksize: int = 250_000) -> pd.DataFrame:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    header = pd.read_csv(path, nrows=0).columns.tolist()
    sym_col = _symbol_col(header)
    if sym_col is None:
        return pd.DataFrame()
    wanted = {sym_col, *columns}
    chunks: list[pd.DataFrame] = []
    try:
        for chunk in pd.read_csv(path, usecols=lambda col: col in wanted, chunksize=chunksize, low_memory=False):
            chunk[sym_col] = chunk[sym_col].fillna("").astype(str).str.upper().str.strip()
            hit = chunk[chunk[sym_col].isin(symbols)].copy()
            if not hit.empty:
                if "symbol" not in hit.columns:
                    hit["symbol"] = hit[sym_col]
                if "ticker" not in hit.columns:
                    hit["ticker"] = hit[sym_col]
                chunks.append(hit)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    if not chunks:
        return pd.DataFrame(columns=[*wanted, "symbol", "ticker"])
    out = pd.concat(chunks, ignore_index=True)
    if "date" in out.columns:
        out["_date_sort"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.sort_values("_date_sort").groupby("symbol", dropna=False).tail(1).drop(columns=["_date_sort"], errors="ignore")
    else:
        out = out.groupby("symbol", dropna=False).tail(1)
    return out


def load_lineage_frames(symbols: set[str], paths: dict[str, Path | None] | None = None) -> dict[str, pd.DataFrame]:
    active = paths or latest_lineage_paths()
    common_cols = {
        "date",
        "company",
        "name",
        "sector",
        "industry",
        "close",
        "adj_close",
        "market_cap",
        "avg_dollar_volume_20d",
        "volatility_20d",
        "trade_action",
        "source_trade_action",
        "model_score",
        "rank_overall",
        "candidate_rank",
        "candidate_rank_overall",
        "directional_strength",
        "confidence_score",
        "risk_adjusted_score",
        "meta_label_probability",
        "expected_trade_return",
        "ticker_direction_bias",
        "ticker_direction_sample_count",
        "ticker_direction_memory_status",
        "target_trade_label_5d",
        "execution_domain",
        "final_execution_side",
        "primary_block_reason",
        "all_block_reasons",
        "approved_notional",
        "suggested_quantity",
        "notional",
        "status",
        "trade_quality_status",
        "trade_quality_reason",
        "order_eligible",
    }
    return {name: _slice_csv(path, symbols, common_cols) for name, path in active.items()}


def _row_for(frames: dict[str, pd.DataFrame], stage: str, symbol: str) -> pd.Series:
    frame = frames.get(stage, pd.DataFrame())
    if frame.empty or "symbol" not in frame.columns:
        return pd.Series(dtype=object)
    hit = frame[frame["symbol"].fillna("").astype(str).str.upper().eq(symbol)]
    return hit.iloc[-1] if not hit.empty else pd.Series(dtype=object)


def _present(frames: dict[str, pd.DataFrame], stage: str, symbol: str) -> bool:
    return not _row_for(frames, stage, symbol).empty


def _source_action(row: pd.Series) -> str:
    return _text(row.get("source_trade_action")) or _text(row.get("trade_action"))


def _price_sanity(screenshot_price: Any, reference_row: pd.Series) -> str:
    price = _num(screenshot_price)
    ref = _num(reference_row.get("close")) or _num(reference_row.get("adj_close"))
    if price is None:
        return "missing_screenshot_price"
    if ref is None:
        return "missing_price_reference"
    diff = abs(price - ref) / ref if ref else 0
    if diff > 0.20:
        return "possible_split_or_scale_issue"
    if diff > 0.05:
        return "stale_price"
    return "plausible"


def _alias_status(symbol: str, frames: dict[str, pd.DataFrame], alias_map: dict[str, str] | None) -> tuple[str, str]:
    if _present(frames, "universe", symbol):
        return "", "exact_match"
    alias = (alias_map or {}).get(symbol, "")
    if alias and _present(frames, "universe", alias):
        return alias, "alias_match"
    if alias:
        return alias, "missing_alias_required"
    return "", "not_found"


def _exclusion_reason(stage: str, prev_present: bool) -> str:
    if prev_present:
        return f"missing_from_{stage}"
    return "upstream_missing"


def _candidate_exclusion(model_row: pd.Series, candidate_present: bool) -> str:
    if candidate_present:
        return ""
    action = _source_action(model_row).lower().replace("_", " ")
    if action == "no decision":
        return "source_trade_action_no_decision"
    if _num(model_row.get("model_score")) is None and _num(model_row.get("rank_overall")) is None:
        return "missing_model_score"
    return "unknown_candidate_exclusion_reason"


def _order_status(order_present: bool, order_row: pd.Series, exec_row: pd.Series) -> str:
    row = order_row if order_present else exec_row
    if row.empty:
        return "not_in_order_plan"
    notional = _num(row.get("approved_notional")) or _num(row.get("notional")) or 0
    qty = _num(row.get("suggested_quantity")) or 0
    if notional > 0 and qty > 0:
        return "order_ready"
    return "missing_sizing"


def _root_cause(row: dict[str, Any]) -> tuple[str, str]:
    checks = [
        ("universe", row["universe_present"], row["universe_exclusion_reason"]),
        ("price_history", row["price_history_present"], row["price_history_exclusion_reason"]),
        ("validated_universe", row["validated_universe_present"], row["validation_exclusion_reason"]),
        ("metadata", row["metadata_present"], row["metadata_exclusion_reason"]),
        ("feature_panel", row["feature_panel_present"], row["feature_panel_exclusion_reason"]),
        ("gold_v2", row["gold_v2_present"], row["gold_exclusion_reason"]),
        ("model_signal", row["model_signal_present"], row["model_signal_exclusion_reason"]),
        ("candidate_pool", row["candidate_pool_present"], row["candidate_exclusion_reason"]),
        ("execution_domain", row["execution_domain"] == "execution_candidate", row["primary_block_reason"] or row["execution_domain"] or "not_execution_candidate"),
        ("order_readiness", row["order_readiness_status"] == "order_ready", row["order_readiness_status"]),
    ]
    action = _text(row.get("source_trade_action")).lower().replace("_", " ")
    if row["model_signal_present"] and action == "no decision":
        return "source_trade_action", row["source_no_decision_reason"] or "unknown_no_decision_reason"
    for stage, present, reason in checks:
        if not present:
            return stage, reason
    return "complete", "order_ready"


def _last_seen(row: dict[str, Any]) -> str:
    last = "requested"
    for stage in STAGES:
        if stage == "source_trade_action":
            if _text(row.get("source_trade_action")).lower().replace("_", " ") in {"long", "short"}:
                last = stage
        elif stage == "execution_domain":
            if _text(row.get("execution_domain")):
                last = stage
        elif stage == "order_plan":
            if row.get("order_plan_present"):
                last = stage
        elif row.get(f"{stage}_present"):
            last = stage
    return last


def _follow_up(root_stage: str, root_reason: str) -> str:
    if root_stage == "universe":
        return "investigate_ticker_mapping_or_universe_filter"
    if root_stage in {"gold_v2", "model_signal", "feature_panel"}:
        return "investigate_gold_model_coverage_gap"
    if root_stage == "source_trade_action":
        return "investigate_source_trade_action_threshold_or_model_score_missing"
    if root_stage == "candidate_pool":
        return "investigate_candidate_pool_preselection"
    if root_stage == "order_readiness":
        return "investigate_execution_candidate_sizing_fields"
    return f"investigate_{root_stage}"


def build_top_mover_lineage(
    movers: pd.DataFrame,
    *,
    frames: dict[str, pd.DataFrame] | None = None,
    paths: dict[str, Path | None] | None = None,
    alias_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    input_frame = movers.copy()
    if "requested_symbol" not in input_frame.columns:
        input_frame = normalize_movers(input_frame["symbol"].astype(str).tolist()) if "symbol" in input_frame.columns else normalize_movers([])
    symbols = set(input_frame["normalized_symbol"].fillna("").astype(str).str.upper())
    active_frames = frames or load_lineage_frames(symbols, paths)
    rows: list[dict[str, Any]] = []
    for _, mover in input_frame.iterrows():
        symbol = _text(mover.get("normalized_symbol")).upper()
        alias, alias_status = _alias_status(symbol, active_frames, alias_map)
        universe = _row_for(active_frames, "universe", symbol)
        price = _row_for(active_frames, "price_history", symbol)
        validated = _row_for(active_frames, "validated_universe", symbol)
        metadata = _row_for(active_frames, "metadata", symbol)
        feature = _row_for(active_frames, "feature_panel", symbol)
        gold = _row_for(active_frames, "gold_v2", symbol)
        signal = _row_for(active_frames, "model_signal", symbol)
        candidate = _row_for(active_frames, "candidate_pool", symbol)
        execution = _row_for(active_frames, "execution_ranked", symbol)
        order = _row_for(active_frames, "order_plan", symbol)
        model_row = signal if not signal.empty else gold
        source_action = _source_action(signal)
        source_reason = source_no_decision_reason(signal) if not signal.empty and source_action.lower().replace("_", " ") == "no decision" else ""
        row = {
            "requested_symbol": mover.get("requested_symbol", symbol),
            "normalized_symbol": symbol,
            "possible_alias_symbol": alias,
            "ticker_mapping_status": alias_status,
            "screenshot_direction": mover.get("screenshot_direction", ""),
            "screenshot_price": mover.get("screenshot_price", ""),
            "price_sanity_status": _price_sanity(mover.get("screenshot_price", ""), gold if not gold.empty else signal),
            "universe_present": not universe.empty,
            "universe_exclusion_reason": "" if not universe.empty else ("ticker_mapping_alias_required" if alias_status in {"alias_match", "missing_alias_required"} else "symbol_not_in_tradable_universe"),
            "price_history_present": not price.empty,
            "price_history_exclusion_reason": "" if not price.empty else _exclusion_reason("price_history", not universe.empty),
            "validated_universe_present": not validated.empty,
            "validation_exclusion_reason": "" if not validated.empty else _exclusion_reason("validated_universe", not price.empty or not universe.empty),
            "metadata_present": not metadata.empty,
            "metadata_exclusion_reason": "" if not metadata.empty else _exclusion_reason("metadata", not validated.empty or not universe.empty),
            "feature_panel_present": not feature.empty,
            "feature_panel_exclusion_reason": "" if not feature.empty else _exclusion_reason("feature_panel", not metadata.empty or not validated.empty),
            "gold_v2_present": not gold.empty,
            "gold_exclusion_reason": "" if not gold.empty else _exclusion_reason("gold_v2", not feature.empty or not universe.empty),
            "model_signal_present": not signal.empty,
            "model_signal_exclusion_reason": "" if not signal.empty else _exclusion_reason("model_signal", not gold.empty),
            "model_score_present": _num(signal.get("model_score")) is not None if not signal.empty else False,
            "model_score": signal.get("model_score", "") if not signal.empty else "",
            "rank_overall": signal.get("rank_overall", signal.get("candidate_rank", "")) if not signal.empty else "",
            "source_trade_action": source_action,
            "source_no_decision_reason": source_reason,
            "ticker_direction_bias": model_row.get("ticker_direction_bias", "") if not model_row.empty else "",
            "ticker_direction_sample_count": model_row.get("ticker_direction_sample_count", "") if not model_row.empty else "",
            "candidate_pool_present": not candidate.empty,
            "candidate_exclusion_reason": _candidate_exclusion(signal, not candidate.empty) if not signal.empty else "",
            "execution_domain": execution.get("execution_domain", "") if not execution.empty else "",
            "final_execution_side": execution.get("final_execution_side", "") if not execution.empty else "",
            "order_plan_present": not order.empty,
            "order_readiness_status": _order_status(not order.empty, order, execution),
            "primary_block_reason": execution.get("primary_block_reason", candidate.get("primary_block_reason", "")) if not execution.empty or not candidate.empty else "",
            "all_block_reasons": execution.get("all_block_reasons", candidate.get("all_block_reasons", "")) if not execution.empty or not candidate.empty else "",
        }
        row["strong_long_missed_by_source_action"] = bool(row["model_signal_present"] and (_num(row["rank_overall"]) or 999999) <= 50 and _text(row["ticker_direction_bias"]).lower() == "trust_long" and _text(row["source_trade_action"]).lower().replace("_", " ") == "no decision")
        row["long_mover_memory_aligned_but_no_decision"] = bool(_text(row["screenshot_direction"]).lower() in {"up", "long", "gainer"} and _text(row["ticker_direction_bias"]).lower() == "trust_long" and _text(row["source_trade_action"]).lower().replace("_", " ") == "no decision")
        row["stage_last_seen"] = _last_seen(row)
        root_stage, root_reason = _root_cause(row)
        row["root_cause_stage"] = root_stage
        row["root_cause_reason"] = root_reason
        row["recommended_follow_up"] = "investigate_source_trade_action_threshold_or_model_score_missing" if row["strong_long_missed_by_source_action"] else _follow_up(root_stage, root_reason)
        rows.append(row)
    return pd.DataFrame(rows)


def _count_true(frame: pd.DataFrame, column: str) -> int:
    return int(frame[column].fillna(False).astype(bool).sum()) if column in frame.columns else 0


def summary_markdown(detail: pd.DataFrame, *, source_label: str = "") -> str:
    source_approved = detail["source_trade_action"].fillna("").astype(str).str.lower().str.replace("_", " ", regex=False).isin(["long", "short"]) if "source_trade_action" in detail.columns else pd.Series(False, index=detail.index)
    funnel = {
        "universe": _count_true(detail, "universe_present"),
        "price_history": _count_true(detail, "price_history_present"),
        "validated_universe": _count_true(detail, "validated_universe_present"),
        "gold": _count_true(detail, "gold_v2_present"),
        "model_signal": _count_true(detail, "model_signal_present"),
        "source_approved": int(source_approved.sum()),
        "candidate_pool": _count_true(detail, "candidate_pool_present"),
        "execution": int(detail.get("execution_domain", pd.Series("", index=detail.index)).fillna("").astype(str).eq("execution_candidate").sum()) if not detail.empty else 0,
        "order_plan": _count_true(detail, "order_plan_present"),
    }
    root_counts = detail.get("root_cause_stage", pd.Series(dtype=str)).fillna("unknown").astype(str).value_counts().to_dict() if not detail.empty else {}
    axon = detail[detail["normalized_symbol"].eq("AXON")] if "normalized_symbol" in detail.columns else pd.DataFrame()
    flex = detail[detail["normalized_symbol"].eq("FLEX")] if "normalized_symbol" in detail.columns else pd.DataFrame()
    lines = [
        "# Top Mover Lineage and Coverage",
        "",
        "## Executive Verdict",
        f"- top_movers_reaching_model: {funnel['model_signal']} / {len(detail)}",
        f"- movers_reaching_candidate_pool: {funnel['candidate_pool']} / {len(detail)}",
        f"- movers_reaching_execution: {funnel['execution']} / {len(detail)}",
        f"- same_day_mover_watch_lane_recommended: {'yes' if funnel['candidate_pool'] == 0 and len(detail) else 'no'}",
        "- safety: diagnostics only; no live trading, no shorts, no gate bypass, no planner-derived execution.",
        "",
        "## Funnel Table",
    ]
    lines.extend([f"- {key}: {value}" for key, value in funnel.items()])
    lines.extend(["", "## Root Cause Breakdown"])
    lines.extend([f"- {key}: {value}" for key, value in root_counts.items()] or ["- none: 0"])
    lines.extend(["", "## Interesting Misses"])
    for label, frame in [("AXON", axon), ("FLEX", flex)]:
        if frame.empty:
            lines.append(f"- {label}: not present in requested movers")
        else:
            row = frame.iloc[0]
            lines.append(f"- {label}: root={row.get('root_cause_stage')} reason={row.get('root_cause_reason')} source_action={row.get('source_trade_action')} rank={row.get('rank_overall')} ticker_memory={row.get('ticker_direction_bias')}")
    high_rank = detail[pd.to_numeric(detail.get("rank_overall", pd.Series(index=detail.index)), errors="coerce").le(50) & detail.get("source_trade_action", pd.Series("", index=detail.index)).fillna("").astype(str).str.lower().eq("no decision")] if not detail.empty else pd.DataFrame()
    if not high_rank.empty:
        lines.append("- rank <= 50 No Decision names: " + ", ".join(high_rank["normalized_symbol"].astype(str).tolist()))
    absent_gold = detail[~detail.get("gold_v2_present", pd.Series(False, index=detail.index)).fillna(False).astype(bool)] if not detail.empty else pd.DataFrame()
    if not absent_gold.empty:
        lines.append("- top movers absent from Gold/model: " + ", ".join(absent_gold["normalized_symbol"].astype(str).tolist()))
    lines.extend(
        [
            "",
            "## Recommendations",
            "- Build same-day top-mover ingestion as watch/shadow first, not execution.",
            "- Investigate source_trade_action thresholds for high-rank trust_long No Decision names.",
            "- Quantify Gold/model coverage gaps before changing candidate gates.",
            "- Add explicit ticker alias mapping for corporate-action names such as PARA before assuming exclusion.",
            "- Keep DFTX-style order readiness separate from direction eligibility; missing sizing must stay non-order-ready.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_top_mover_lineage(
    movers: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    paths: dict[str, Path | None] | None = None,
    frames: dict[str, pd.DataFrame] | None = None,
    alias_map: dict[str, str] | None = None,
) -> TopMoverLineageOutput:
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    detail = build_top_mover_lineage(movers, frames=frames, paths=paths, alias_map=alias_map)
    detail_path = out_dir / f"top_mover_lineage_detail_{run_stamp}.csv"
    summary_path = out_dir / f"top_mover_lineage_summary_{run_stamp}.md"
    detail.to_csv(detail_path, index=False)
    summary_path.write_text(summary_markdown(detail), encoding="utf-8")
    return TopMoverLineageOutput(detail_path=detail_path, summary_path=summary_path, detail=detail)
