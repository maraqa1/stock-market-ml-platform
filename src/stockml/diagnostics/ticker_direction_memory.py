from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp
from stockml.trading.ticker_direction_memory import (
    BIAS_INVERSE_WATCH,
    BIAS_INSUFFICIENT_DATA,
    BIAS_TRUST_ORIGINAL,
    MEMORY_COLUMNS,
    normalize_direction_outcomes,
    summarize_ticker_direction_memory,
)


SOURCE_PATTERNS = [
    "direction_inversion_open_positions_*.csv",
    "trade_inverse_outcome_*.csv",
    "inverse_strategy_diagnostic_*.csv",
]


def _latest_source(root: Path) -> Path | None:
    diag = root / "data" / "trading" / "diagnostics"
    files: list[Path] = []
    for pattern in SOURCE_PATTERNS:
        files.extend([path for path in diag.glob(pattern) if path.is_file()])
    return max(files, key=lambda item: item.stat().st_mtime) if files else None


def _read_source(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _write_markdown(memory: pd.DataFrame, path: Path, *, source_path: Path | None) -> None:
    counts = memory["ticker_direction_bias"].value_counts().to_dict() if not memory.empty else {}
    inverse = memory[memory["ticker_direction_bias"].eq(BIAS_INVERSE_WATCH)].head(20) if not memory.empty else pd.DataFrame()
    trusted = memory[memory["ticker_direction_bias"].eq(BIAS_TRUST_ORIGINAL)].head(20) if not memory.empty else pd.DataFrame()
    sparse = memory[memory["ticker_direction_bias"].eq(BIAS_INSUFFICIENT_DATA)].head(20) if not memory.empty else pd.DataFrame()

    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Ticker Direction Memory\n\n")
        handle.write(f"- Generated at UTC: `{datetime.now(timezone.utc).isoformat()}`\n")
        handle.write(f"- Source: `{source_path or ''}`\n")
        handle.write(f"- Tickers analysed: `{len(memory)}`\n")
        handle.write(f"- trust_original: `{counts.get(BIAS_TRUST_ORIGINAL, 0)}`\n")
        handle.write(f"- inverse_watch: `{counts.get(BIAS_INVERSE_WATCH, 0)}`\n")
        handle.write(f"- insufficient_data: `{counts.get(BIAS_INSUFFICIENT_DATA, 0)}`\n\n")
        handle.write("## Policy\n\n")
        handle.write(
            "Ticker memory is evidence, not an order instruction. A ticker with `inverse_watch` should be blocked for "
            "manual or research review; the system must not silently flip direction.\n\n"
        )
        for title, sample in [
            ("Inverse Watch Tickers", inverse),
            ("Original Direction Supported", trusted),
            ("Sparse Ticker Evidence", sparse),
        ]:
            handle.write(f"## {title}\n\n")
            if sample.empty:
                handle.write("None.\n\n")
                continue
            columns = [
                "symbol",
                "sample_count",
                "avg_original_return_bps",
                "avg_inverse_return_bps",
                "inverse_advantage_bps",
                "ticker_direction_bias",
                "ticker_direction_reason",
            ]
            sample = sample[[column for column in columns if column in sample.columns]].copy()
            handle.write("| " + " | ".join(sample.columns) + " |\n")
            handle.write("| " + " | ".join(["---"] * len(sample.columns)) + " |\n")
            for row in sample.fillna("").astype(str).to_dict("records"):
                handle.write("| " + " | ".join(str(row.get(column, "")).replace("|", "/") for column in sample.columns) + " |\n")
            handle.write("\n")


def build_ticker_direction_memory_report(source: pd.DataFrame) -> pd.DataFrame:
    outcomes = normalize_direction_outcomes(source)
    memory = summarize_ticker_direction_memory(outcomes)
    if memory.empty:
        return pd.DataFrame(columns=MEMORY_COLUMNS)
    return memory


def run_ticker_direction_memory(
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
) -> dict[str, Any]:
    base = Path(root) if root else PROJECT_ROOT
    out_dir = Path(output_dir) if output_dir else base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    source_path = _latest_source(base)
    source = _read_source(source_path)
    memory = build_ticker_direction_memory_report(source)
    run_stamp = stamp or timestamp()
    csv_path = out_dir / f"ticker_direction_memory_{run_stamp}.csv"
    md_path = out_dir / f"ticker_direction_memory_{run_stamp}.md"
    memory.to_csv(csv_path, index=False)
    _write_markdown(memory, md_path, source_path=source_path)
    counts = memory["ticker_direction_bias"].value_counts().to_dict() if not memory.empty else {}
    status = "ok" if source_path is not None and not memory.empty else "missing_data"
    if status == "ok" and counts.get(BIAS_INSUFFICIENT_DATA, 0) == len(memory):
        status = "insufficient_data"
    return {
        "status": status,
        "source_path": str(source_path or ""),
        "csv_path": str(csv_path),
        "markdown_path": str(md_path),
        "rows": len(memory),
        "trust_original": int(counts.get(BIAS_TRUST_ORIGINAL, 0)),
        "inverse_watch": int(counts.get(BIAS_INVERSE_WATCH, 0)),
        "insufficient_data": int(counts.get(BIAS_INSUFFICIENT_DATA, 0)),
    }
