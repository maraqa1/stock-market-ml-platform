from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.diagnostics.top_mover_lineage import DIAGNOSTIC_DIR, latest_lineage_paths


@dataclass(frozen=True)
class GoldModelCoverageOutput:
    csv_path: Path
    summary_path: Path
    frame: pd.DataFrame


def _symbol_set(path: Path | None, *, chunksize: int = 300_000) -> set[str]:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return set()
    columns = pd.read_csv(path, nrows=0).columns.tolist()
    symbol_col = "symbol" if "symbol" in columns else "ticker" if "ticker" in columns else None
    if symbol_col is None:
        return set()
    out: set[str] = set()
    try:
        for chunk in pd.read_csv(path, usecols=[symbol_col], chunksize=chunksize, low_memory=False):
            out.update(chunk[symbol_col].dropna().astype(str).str.upper().str.strip().tolist())
    except pd.errors.EmptyDataError:
        return set()
    return {symbol for symbol in out if symbol and symbol.lower() not in {"nan", "none"}}


def _joined(values: set[str], limit: int = 100) -> str:
    items = sorted(values)
    suffix = "" if len(items) <= limit else f"...(+{len(items) - limit} more)"
    return ",".join(items[:limit]) + suffix


def build_gold_model_coverage_audit(
    *,
    paths: dict[str, Path | None] | None = None,
    mover_symbols: set[str] | None = None,
) -> pd.DataFrame:
    active = paths or latest_lineage_paths()
    universe = _symbol_set(active.get("universe"))
    price = _symbol_set(active.get("price_history"))
    validated = _symbol_set(active.get("validated_universe"))
    metadata = _symbol_set(active.get("metadata"))
    feature = _symbol_set(active.get("feature_panel"))
    gold = _symbol_set(active.get("gold_v2"))
    model = _symbol_set(active.get("model_signal"))
    candidates = _symbol_set(active.get("candidate_pool"))
    movers = {symbol.upper() for symbol in (mover_symbols or set())}
    missing_gold = universe - gold
    missing_model = gold - model
    rows = [
        {
            "tradable_universe_count": len(universe),
            "price_history_count": len(price),
            "validated_universe_count": len(validated),
            "metadata_count": len(metadata),
            "feature_panel_count": len(feature),
            "gold_v2_count": len(gold),
            "model_signal_count": len(model),
            "candidate_pool_count": len(candidates),
            "missing_from_gold_count": len(missing_gold),
            "missing_from_model_signal_count": len(missing_model),
            "symbols_in_universe_not_gold": _joined(missing_gold),
            "symbols_in_gold_not_model": _joined(missing_model),
            "top_movers_missing_from_gold": _joined(movers - gold),
            "top_movers_missing_from_model": _joined(movers - model),
            "most_common_gold_exclusion_reasons": "not_in_gold_v2",
            "most_common_model_exclusion_reasons": "not_in_model_signal",
        }
    ]
    return pd.DataFrame(rows)


def coverage_summary_markdown(frame: pd.DataFrame) -> str:
    row: dict[str, Any] = frame.iloc[0].to_dict() if not frame.empty else {}
    lines = [
        "# Gold / Model Coverage Audit",
        "",
        f"- tradable_universe_count: {row.get('tradable_universe_count', 0)}",
        f"- price_history_count: {row.get('price_history_count', 0)}",
        f"- validated_universe_count: {row.get('validated_universe_count', 0)}",
        f"- metadata_count: {row.get('metadata_count', 0)}",
        f"- feature_panel_count: {row.get('feature_panel_count', 0)}",
        f"- gold_v2_count: {row.get('gold_v2_count', 0)}",
        f"- model_signal_count: {row.get('model_signal_count', 0)}",
        f"- candidate_pool_count: {row.get('candidate_pool_count', 0)}",
        f"- missing_from_gold_count: {row.get('missing_from_gold_count', 0)}",
        f"- missing_from_model_signal_count: {row.get('missing_from_model_signal_count', 0)}",
        f"- top_movers_missing_from_gold: {row.get('top_movers_missing_from_gold', '')}",
        f"- top_movers_missing_from_model: {row.get('top_movers_missing_from_model', '')}",
        "",
        "This audit is diagnostic only and does not change model scoring, candidate selection, or trading behavior.",
    ]
    return "\n".join(lines) + "\n"


def write_gold_model_coverage_audit(
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    paths: dict[str, Path | None] | None = None,
    mover_symbols: set[str] | None = None,
) -> GoldModelCoverageOutput:
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    frame = build_gold_model_coverage_audit(paths=paths, mover_symbols=mover_symbols)
    csv_path = out_dir / f"gold_model_coverage_audit_{run_stamp}.csv"
    summary_path = out_dir / f"gold_model_coverage_summary_{run_stamp}.md"
    frame.to_csv(csv_path, index=False)
    summary_path.write_text(coverage_summary_markdown(frame), encoding="utf-8")
    return GoldModelCoverageOutput(csv_path=csv_path, summary_path=summary_path, frame=frame)
