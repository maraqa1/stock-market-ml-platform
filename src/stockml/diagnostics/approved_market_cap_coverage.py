from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp


DETAIL_COLUMNS = [
    "symbol",
    "source_trade_action",
    "trade_action",
    "candidate_rank",
    "final_execution_side",
    "candidate_market_cap",
    "primary_block_reason",
    "all_block_reasons",
    "in_validated_universe",
    "metadata_present",
    "metadata_market_cap",
    "metadata_status",
    "metadata_error",
    "gold_present",
    "gold_market_cap",
    "candidate_pool_path",
    "validated_universe_path",
    "metadata_path",
    "gold_path",
    "missing_market_cap_root_cause",
    "diagnostic_decision",
]


SOURCE_APPROVED = {"long", "short", "buy", "sell"}


def _latest(directory: Path, pattern: str) -> Path | None:
    files = sorted(directory.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _read_csv(path: Path | str | None, columns: Iterable[str] | None = None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    source = Path(path)
    if not source.exists() or not source.is_file() or source.stat().st_size == 0:
        return pd.DataFrame()
    try:
        header = pd.read_csv(source, nrows=0)
    except Exception:
        return pd.DataFrame()
    if columns is not None:
        wanted = [column for column in columns if column in header.columns]
        if not wanted:
            return pd.DataFrame()
        return pd.read_csv(source, usecols=wanted, low_memory=False)
    return pd.read_csv(source, low_memory=False)


def _symbol_col(frame: pd.DataFrame) -> str | None:
    for column in ["symbol", "ticker", "yahoo_ticker"]:
        if column in frame.columns:
            return column
    return None


def _norm_symbols(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    column = _symbol_col(frame)
    if not column:
        return pd.DataFrame()
    out = frame.copy()
    out["symbol"] = out[column].fillna("").astype(str).str.upper().str.strip()
    return out[out["symbol"].ne("")]


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
        if value is None or pd.isna(value):
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _latest_gold_row(gold: pd.DataFrame) -> pd.DataFrame:
    if gold.empty:
        return gold
    out = gold.copy()
    if "date" in out.columns:
        out["__date"] = pd.to_datetime(out["date"], errors="coerce")
        out = out.sort_values(["symbol", "__date"]).drop_duplicates("symbol", keep="last")
        return out.drop(columns=["__date"])
    return out.drop_duplicates("symbol", keep="last")


def _index(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "symbol" not in frame.columns:
        return pd.DataFrame()
    return frame.drop_duplicates("symbol", keep="last").set_index("symbol", drop=False)


def _root_cause(*, in_validated: bool, metadata_present: bool, metadata_cap: float | None, gold_present: bool, gold_cap: float | None) -> str:
    if metadata_present and metadata_cap is not None:
        return "candidate_metadata_join_failure"
    if gold_present and gold_cap is not None:
        return "candidate_gold_join_failure"
    if metadata_present and metadata_cap is None:
        return "provider_uncovered_market_cap"
    if in_validated and not metadata_present:
        return "metadata_fetch_or_join_gap"
    if not in_validated:
        return "validated_universe_exclusion_or_stale_candidate"
    if gold_present and gold_cap is None:
        return "gold_market_cap_uncovered"
    return "unknown"


def _decision(root_cause: str) -> str:
    if root_cause in {"candidate_metadata_join_failure", "candidate_gold_join_failure", "metadata_fetch_or_join_gap"}:
        return "pipeline_join_or_fetch_bug"
    if root_cause in {"provider_uncovered_market_cap", "gold_market_cap_uncovered"}:
        return "provider_coverage_gap"
    if root_cause == "validated_universe_exclusion_or_stale_candidate":
        return "stale_or_unvalidated_candidate"
    return "needs_manual_review"


def build_approved_market_cap_coverage_report(
    *,
    root: Path | str | None = None,
    candidate_file: Path | str | None = None,
    metadata_file: Path | str | None = None,
    validated_file: Path | str | None = None,
    gold_file: Path | str | None = None,
) -> pd.DataFrame:
    base = Path(root).resolve() if root else PROJECT_ROOT
    data = base / "data"
    candidate_path = Path(candidate_file) if candidate_file else _latest(data / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    metadata_path = Path(metadata_file) if metadata_file else _latest(data / "interim", "04_us_metadata_enriched_*.csv")
    validated_path = Path(validated_file) if validated_file else _latest(data / "interim", "03_us_price_validated_universe_*.csv")
    gold_path = Path(gold_file) if gold_file else (
        _latest(data / "gold", "gold_stock_decision_daily_*.csv") or _latest(data / "gold", "06_us_gold_ml_dataset_*.csv")
    )

    candidates = _norm_symbols(
        _read_csv(
            candidate_path,
            [
                "symbol",
                "ticker",
                "source_trade_action",
                "trade_action",
                "candidate_rank",
                "raw_rank",
                "final_execution_side",
                "market_cap",
                "primary_block_reason",
                "all_block_reasons",
            ],
        )
    )
    if candidates.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    source_action = candidates.get("source_trade_action", pd.Series("", index=candidates.index)).fillna("").astype(str).str.lower()
    candidate_cap = pd.to_numeric(candidates.get("market_cap", pd.Series(pd.NA, index=candidates.index)), errors="coerce")
    target = candidates[source_action.isin(SOURCE_APPROVED) & candidate_cap.isna()].copy()
    if target.empty:
        return pd.DataFrame(columns=DETAIL_COLUMNS)

    metadata = _index(
        _norm_symbols(
            _read_csv(
                metadata_path,
                ["symbol", "ticker", "yahoo_ticker", "market_cap", "metadata_status", "metadata_error", "sector", "industry"],
            )
        )
    )
    validated = _norm_symbols(_read_csv(validated_path, ["symbol", "ticker", "yahoo_ticker"]))
    validated_symbols = set(validated["symbol"]) if not validated.empty and "symbol" in validated.columns else set()
    gold = _index(
        _latest_gold_row(
            _norm_symbols(
                _read_csv(
                    gold_path,
                    ["symbol", "ticker", "date", "market_cap", "sector", "industry", "source_trade_action", "trade_action"],
                )
            )
        )
    )

    rows: list[dict[str, Any]] = []
    for _, row in target.sort_values(["candidate_rank", "symbol"], na_position="last").iterrows():
        symbol = _text(row.get("symbol")).upper()
        metadata_row = metadata.loc[symbol] if symbol in metadata.index else pd.Series(dtype=object)
        gold_row = gold.loc[symbol] if symbol in gold.index else pd.Series(dtype=object)
        metadata_present = symbol in metadata.index
        gold_present = symbol in gold.index
        metadata_cap = _num(metadata_row.get("market_cap")) if metadata_present else None
        gold_cap = _num(gold_row.get("market_cap")) if gold_present else None
        in_validated = symbol in validated_symbols
        root_cause = _root_cause(
            in_validated=in_validated,
            metadata_present=metadata_present,
            metadata_cap=metadata_cap,
            gold_present=gold_present,
            gold_cap=gold_cap,
        )
        rows.append(
            {
                "symbol": symbol,
                "source_trade_action": _text(row.get("source_trade_action")),
                "trade_action": _text(row.get("trade_action")),
                "candidate_rank": row.get("candidate_rank", row.get("raw_rank", "")),
                "final_execution_side": _text(row.get("final_execution_side")),
                "candidate_market_cap": row.get("market_cap", pd.NA),
                "primary_block_reason": _text(row.get("primary_block_reason")),
                "all_block_reasons": _text(row.get("all_block_reasons")),
                "in_validated_universe": in_validated,
                "metadata_present": metadata_present,
                "metadata_market_cap": metadata_cap,
                "metadata_status": _text(metadata_row.get("metadata_status")) if metadata_present else "",
                "metadata_error": _text(metadata_row.get("metadata_error")) if metadata_present else "",
                "gold_present": gold_present,
                "gold_market_cap": gold_cap,
                "candidate_pool_path": str(candidate_path or ""),
                "validated_universe_path": str(validated_path or ""),
                "metadata_path": str(metadata_path or ""),
                "gold_path": str(gold_path or ""),
                "missing_market_cap_root_cause": root_cause,
                "diagnostic_decision": _decision(root_cause),
            }
        )
    return pd.DataFrame(rows, columns=DETAIL_COLUMNS)


def write_approved_market_cap_coverage_report(
    *,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    run_stamp = stamp or timestamp()
    out_dir = Path(output_dir) if output_dir else base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = build_approved_market_cap_coverage_report(root=base, **kwargs)
    csv_path = out_dir / f"approved_market_cap_coverage_{run_stamp}.csv"
    md_path = out_dir / f"approved_market_cap_coverage_{run_stamp}.md"
    detail.to_csv(csv_path, index=False)

    root_counts = detail["missing_market_cap_root_cause"].value_counts().to_dict() if not detail.empty else {}
    decision_counts = detail["diagnostic_decision"].value_counts().to_dict() if not detail.empty else {}
    lines = [
        "# Approved Market Cap Coverage Diagnostic",
        "",
        f"- rows: {len(detail)}",
        f"- root_cause_counts: {root_counts}",
        f"- decision_counts: {decision_counts}",
        "",
        "This diagnostic is read-only. It does not change gates, config, ranking, sizing, or broker submission.",
    ]
    if not detail.empty:
        lines.extend(["", "## Symbols", ""])
        for row in detail.to_dict("records"):
            lines.append(
                f"- {row['symbol']}: {row['missing_market_cap_root_cause']} "
                f"(metadata_present={row['metadata_present']}, gold_present={row['gold_present']}, "
                f"in_validated={row['in_validated_universe']})"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "status": "ok",
        "rows": len(detail),
        "path": str(csv_path),
        "summary_path": str(md_path),
        "root_cause_counts": root_counts,
        "decision_counts": decision_counts,
    }
