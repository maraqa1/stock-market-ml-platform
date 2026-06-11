from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

from stockml.common.paths import PROJECT_ROOT, latest_file, timestamp


TRACE_DIR_NAME = "intraday_handoff"
TRACE_COLUMNS = [
    "trace_at",
    "stage",
    "symbol",
    "side",
    "status",
    "score",
    "reason",
    "selected_in_order_plan",
    "order_result_status",
    "order_result_message",
    "source_file",
]


def write_intraday_handoff_trace(
    *,
    root: Path | str | None = None,
    refresh: dict[str, Any] | None = None,
    scoring: dict[str, Any] | None = None,
    forecast: dict[str, Any] | None = None,
    autopilot: dict[str, Any] | None = None,
    snapshot: dict[str, Any] | None = None,
    stamp: str | None = None,
    top_n: int = 30,
) -> dict[str, Any]:
    base = Path(root) if root is not None else PROJECT_ROOT
    trace_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    artifacts = _latest_artifacts(base, forecast=forecast, snapshot=snapshot)
    order_plan = _read_csv(artifacts["order_plan"])
    order_results = _read_csv(artifacts["order_results"])
    selected_symbols = _symbols(order_plan)
    result_by_symbol = _rows_by_symbol(order_results)

    rows: list[dict[str, Any]] = []
    rows.extend(_summary_rows(trace_at, refresh, scoring, forecast, autopilot, snapshot))
    rows.extend(
        _candidate_rows(
            "candidate_pool",
            _read_csv(artifacts["candidate_pool"]),
            trace_at,
            artifacts["candidate_pool"],
            selected_symbols,
            result_by_symbol,
            top_n=top_n,
        )
    )
    rows.extend(
        _candidate_rows(
            "per_symbol_forecast",
            _read_csv(artifacts["per_symbol_forecast"]),
            trace_at,
            artifacts["per_symbol_forecast"],
            selected_symbols,
            result_by_symbol,
            top_n=top_n,
        )
    )
    rows.extend(
        _candidate_rows(
            "trading_snapshot",
            _read_csv(artifacts["snapshot"]),
            trace_at,
            artifacts["snapshot"],
            selected_symbols,
            result_by_symbol,
            top_n=top_n,
        )
    )

    output_dir = base / "data" / "trading" / TRACE_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = stamp or timestamp()
    csv_path = output_dir / f"intraday_handoff_trace_{suffix}.csv"
    markdown_path = output_dir / f"intraday_handoff_summary_{suffix}.md"
    frame = pd.DataFrame(rows, columns=TRACE_COLUMNS)
    frame.to_csv(csv_path, index=False)
    markdown_path.write_text(_summary_markdown(frame, artifacts), encoding="utf-8")

    return {
        "status": "ok",
        "rows": len(frame),
        "path": str(csv_path),
        "summary_path": str(markdown_path),
        "top_candidate_rows": int((frame["stage"] == "candidate_pool").sum()) if not frame.empty else 0,
        "selected_symbols": len(selected_symbols),
    }


def _latest_artifacts(base: Path, *, forecast: dict[str, Any] | None, snapshot: dict[str, Any] | None) -> dict[str, Path | None]:
    data = base / "data"
    forecast_path = _path_from_result(forecast, "path") or latest_file(data / "trading" / "per_symbol_forecast", "per_symbol_forecast_*.csv")
    snapshot_path = _path_from_result(snapshot, "path") or latest_file(data / "trading" / "snapshots", "trading_snapshot_*.csv")
    return {
        "candidate_pool": latest_file(data / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv"),
        "order_plan": latest_file(data / "portal_outputs", "08_alpaca_paper_order_plan_*.csv"),
        "order_results": latest_file(data / "portal_outputs", "08_alpaca_paper_order_results_*.csv"),
        "per_symbol_forecast": forecast_path,
        "snapshot": snapshot_path,
    }


def _path_from_result(result: dict[str, Any] | None, key: str) -> Path | None:
    if not result:
        return None
    raw = result.get(key)
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.exists() else None


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except (EmptyDataError, OSError, ValueError):
        return pd.DataFrame()


def _summary_rows(
    trace_at: str,
    refresh: dict[str, Any] | None,
    scoring: dict[str, Any] | None,
    forecast: dict[str, Any] | None,
    autopilot: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    rows = []
    for stage, result, count_key in (
        ("candidate_refresh", refresh or {}, "snapshots_written"),
        ("intraday_promotion", scoring or {}, "snapshots_scored"),
        ("per_symbol_forecast_summary", forecast or {}, "rows"),
        ("paper_autopilot", autopilot or {}, "autopilot_open_submitted"),
        ("trading_snapshot_summary", snapshot or {}, "rows"),
    ):
        rows.append(
            {
                "trace_at": trace_at,
                "stage": stage,
                "symbol": "",
                "side": "",
                "status": result.get("status") or result.get("phase") or "",
                "score": result.get(count_key, ""),
                "reason": _summary_reason(result),
                "selected_in_order_plan": "",
                "order_result_status": "",
                "order_result_message": "",
                "source_file": result.get("path", ""),
            }
        )
    return rows


def _summary_reason(result: dict[str, Any]) -> str:
    for key in ("reason", "autopilot_open_notes", "last_error"):
        value = result.get(key)
        if value:
            return str(value)
    if "verdict_counts" in result:
        return str(result.get("verdict_counts") or {})
    return ""


def _candidate_rows(
    stage: str,
    frame: pd.DataFrame,
    trace_at: str,
    source_file: Path | None,
    selected_symbols: set[str],
    result_by_symbol: dict[str, dict[str, Any]],
    *,
    top_n: int,
) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    symbol_col = _symbol_column(frame)
    if not symbol_col:
        return []
    working = frame.copy()
    working["__symbol"] = working[symbol_col].fillna("").astype(str).str.upper().str.strip()
    working = working[working["__symbol"].ne("")]
    if working.empty:
        return []
    score_col = _score_column(working)
    if score_col:
        working["__score"] = pd.to_numeric(working[score_col], errors="coerce")
        working = working.sort_values("__score", ascending=False, na_position="last")
    working = working.drop_duplicates("__symbol").head(max(1, top_n))

    rows = []
    for row in working.fillna("").to_dict("records"):
        symbol = str(row.get("__symbol") or "")
        result = result_by_symbol.get(symbol, {})
        rows.append(
            {
                "trace_at": trace_at,
                "stage": stage,
                "symbol": symbol,
                "side": _first_value(row, ("side", "trade_action", "action", "direction")),
                "status": _first_value(row, ("trade_quality_status", "verdict", "final_verdict", "forecast_confirmation", "status")),
                "score": row.get("__score", "") if score_col else "",
                "reason": _first_value(row, ("trade_quality_reason", "reason", "block_reason", "outcome_reason", "confirmation_reason")),
                "selected_in_order_plan": symbol in selected_symbols,
                "order_result_status": _first_value(result, ("status", "alpaca_status", "result_status")),
                "order_result_message": _first_value(result, ("message", "api_error", "reject_reason")),
                "source_file": str(source_file or ""),
            }
        )
    return rows


def _symbols(frame: pd.DataFrame) -> set[str]:
    if frame.empty:
        return set()
    col = _symbol_column(frame)
    if not col:
        return set()
    return set(frame[col].fillna("").astype(str).str.upper().str.strip()) - {""}


def _rows_by_symbol(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if frame.empty:
        return {}
    col = _symbol_column(frame)
    if not col:
        return {}
    working = frame.copy()
    working["__symbol"] = working[col].fillna("").astype(str).str.upper().str.strip()
    working = working[working["__symbol"].ne("")]
    return {str(row["__symbol"]): {k: v for k, v in row.items() if k != "__symbol"} for row in working.fillna("").to_dict("records")}


def _symbol_column(frame: pd.DataFrame) -> str | None:
    for column in ("symbol", "ticker", "yahoo_ticker"):
        if column in frame.columns:
            return column
    return None


def _score_column(frame: pd.DataFrame) -> str | None:
    for column in (
        "risk_adjusted_score",
        "display_score",
        "score",
        "raw_score",
        "selection_score",
        "expected_trade_return",
        "volatility_adjusted_score",
        "risk_adjusted_forecast_score",
        "expected_profitability_score",
        "model_score",
    ):
        if column in frame.columns:
            return column
    return None


def _first_value(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if not isinstance(value, (dict, list)) and pd.isna(value):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _summary_markdown(frame: pd.DataFrame, artifacts: dict[str, Path | None]) -> str:
    lines = ["# Intraday Handoff Trace", ""]
    lines.append("## Artifacts")
    for name, path in artifacts.items():
        lines.append(f"- {name}: {path or 'missing'}")
    lines.extend(["", "## Counts"])
    if frame.empty:
        lines.append("- no rows")
    else:
        for stage, count in frame["stage"].value_counts().sort_index().items():
            lines.append(f"- {stage}: {count}")
    if not frame.empty and "selected_in_order_plan" in frame:
        selected = frame[frame["selected_in_order_plan"].astype(str).str.lower().eq("true")]
        if not selected.empty:
            lines.extend(["", "## Selected Symbols"])
            lines.append(", ".join(selected["symbol"].dropna().astype(str).head(50).tolist()))
    lines.append("")
    return "\n".join(lines)
