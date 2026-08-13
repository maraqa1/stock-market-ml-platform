from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp


AI2_FILE_PATTERNS = (
    "ai2_enriched_execution_ranked_candidates_*.csv",
    "ai2_candidate_input_*.shortlist.csv",
)

FIELD_GROUPS = {
    "realtime_quote": ("ai2_realtime_price", "ai2_quote_timestamp", "ai2_quote_age_seconds"),
    "technical": ("ai2_sma_20", "ai2_sma_50", "ai2_rsi_14", "ai2_atr_14"),
    "news_sentiment": ("ai2_news_count", "ai2_sentiment_score", "ai2_news_attention_score"),
    "exchange_identity": ("ai2_exchange", "ai2_currency", "ai2_security_type"),
}


@dataclass(frozen=True)
class AI2ExtendedEnrichmentDiagnosticResult:
    status: str
    rows: int
    source_path: Path | None
    detail_path: Path
    summary_path: Path
    group_coverage: dict[str, int]
    missing_columns: dict[str, list[str]]


def latest_ai2_enriched_candidate_path(root: Path | str | None = None) -> Path | None:
    base = Path(root) if root else PROJECT_ROOT
    output_dir = base / "data" / "portal_outputs"
    files: list[Path] = []
    for pattern in AI2_FILE_PATTERNS:
        files.extend(output_dir.glob(pattern))
    if not files:
        return None
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def _read_csv(path: Path | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _series(frame: pd.DataFrame, column: str, *, numeric: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NA, index=frame.index)
    if numeric:
        return pd.to_numeric(frame[column], errors="coerce")
    return frame[column].replace("", pd.NA)


def _complete_rows(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.Series:
    if frame.empty or any(column not in frame.columns for column in columns):
        return pd.Series(False, index=frame.index)
    return frame.loc[:, list(columns)].replace("", pd.NA).notna().all(axis=1)


def _group_coverage(frame: pd.DataFrame) -> tuple[dict[str, int], dict[str, list[str]]]:
    coverage: dict[str, int] = {}
    missing: dict[str, list[str]] = {}
    for group, columns in FIELD_GROUPS.items():
        missing[group] = [column for column in columns if column not in frame.columns]
        coverage[group] = int(_complete_rows(frame, columns).sum()) if not frame.empty else 0
    return coverage, missing


def _quote_status(frame: pd.DataFrame, *, max_quote_age_seconds: float) -> pd.Series:
    price = _series(frame, "ai2_realtime_price", numeric=True)
    age = _series(frame, "ai2_quote_age_seconds", numeric=True)
    status = pd.Series("missing_quote_fields", index=frame.index)
    has_quote = price.notna() & price.gt(0) & age.notna()
    status.loc[has_quote & age.le(max_quote_age_seconds)] = "quote_fresh"
    status.loc[has_quote & age.gt(max_quote_age_seconds)] = "quote_stale"
    return status


def _technical_status(frame: pd.DataFrame) -> pd.Series:
    rsi = _series(frame, "ai2_rsi_14", numeric=True)
    sma_20 = _series(frame, "ai2_sma_20", numeric=True)
    status = pd.Series("missing_technical_fields", index=frame.index)
    has_technical = rsi.notna() | sma_20.notna()
    status.loc[has_technical] = "technical_available"
    status.loc[rsi.ge(75)] = "technical_overbought_watch"
    status.loc[rsi.le(25)] = "technical_oversold_watch"
    return status


def _news_status(frame: pd.DataFrame) -> pd.Series:
    count = _series(frame, "ai2_news_count", numeric=True).fillna(0)
    sentiment = _series(frame, "ai2_sentiment_score", numeric=True)
    status = pd.Series("missing_news_fields", index=frame.index)
    has_news_fields = count.gt(0) | sentiment.notna()
    status.loc[has_news_fields] = "news_available"
    status.loc[count.gt(0) & sentiment.le(-0.25)] = "negative_news_watch"
    status.loc[count.gt(0) & sentiment.ge(0.25)] = "positive_news_support"
    return status


def _identity_status(frame: pd.DataFrame) -> pd.Series:
    exchange = _series(frame, "ai2_exchange")
    currency = _series(frame, "ai2_currency")
    security_type = _series(frame, "ai2_security_type")
    status = pd.Series("missing_identity_fields", index=frame.index)
    status.loc[exchange.notna() & currency.notna() & security_type.notna()] = "identity_available"
    return status


def _recommendation(frame: pd.DataFrame) -> pd.Series:
    recommendation = pd.Series("extra_enrichment_clear", index=frame.index)
    missing_all = (
        frame["ai2_quote_status"].eq("missing_quote_fields")
        & frame["ai2_technical_status"].eq("missing_technical_fields")
        & frame["ai2_news_status"].eq("missing_news_fields")
        & frame["ai2_identity_status"].eq("missing_identity_fields")
    )
    recommendation.loc[missing_all] = "no_extra_enrichment_available"
    recommendation.loc[frame["ai2_quote_status"].eq("quote_stale")] = "would_refresh_quote"
    recommendation.loc[frame["ai2_technical_status"].isin(["technical_overbought_watch", "technical_oversold_watch"])] = "would_review_technical"
    recommendation.loc[frame["ai2_news_status"].eq("negative_news_watch")] = "would_review_news"
    return recommendation


def enrich_with_ai2_extended_diagnostics(
    frame: pd.DataFrame,
    *,
    max_quote_age_seconds: float = 900.0,
) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        for column in [
            "ai2_quote_status",
            "ai2_technical_status",
            "ai2_news_status",
            "ai2_identity_status",
            "ai2_extended_enrichment_recommendation",
        ]:
            out[column] = []
        return out
    out["ai2_quote_status"] = _quote_status(out, max_quote_age_seconds=max_quote_age_seconds)
    out["ai2_technical_status"] = _technical_status(out)
    out["ai2_news_status"] = _news_status(out)
    out["ai2_identity_status"] = _identity_status(out)
    out["ai2_extended_enrichment_recommendation"] = _recommendation(out)
    return out


def _write_summary(
    path: Path,
    *,
    source_path: Path | None,
    frame: pd.DataFrame,
    coverage: dict[str, int],
    missing: dict[str, list[str]],
) -> None:
    rows = len(frame)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# AI2 Extended Enrichment Diagnostic\n\n")
        handle.write(f"- Source file: `{source_path or ''}`\n")
        handle.write(f"- Rows: `{rows}`\n")
        handle.write("- Trading behavior changed: `false`\n\n")
        handle.write("## Field Group Coverage\n\n")
        for group, count in coverage.items():
            pct = round((count / rows) * 100, 2) if rows else 0.0
            handle.write(f"- {group}: `{count}` rows (`{pct}%`)\n")
        handle.write("\n## Missing Columns\n\n")
        for group, columns in missing.items():
            text = ", ".join(columns) if columns else "none"
            handle.write(f"- {group}: `{text}`\n")
        handle.write("\n## Diagnostic Recommendations\n\n")
        if frame.empty or "ai2_extended_enrichment_recommendation" not in frame.columns:
            handle.write("- no rows\n")
            return
        for key, value in frame["ai2_extended_enrichment_recommendation"].fillna("").astype(str).value_counts().to_dict().items():
            handle.write(f"- {key}: `{value}`\n")


def run_ai2_extended_enrichment_diagnostics(
    *,
    candidate_file: Path | str | None = None,
    root: Path | str | None = None,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    max_quote_age_seconds: float = 900.0,
) -> AI2ExtendedEnrichmentDiagnosticResult:
    base = Path(root) if root else PROJECT_ROOT
    source_path = Path(candidate_file) if candidate_file else latest_ai2_enriched_candidate_path(base)
    source = _read_csv(source_path)
    detail = enrich_with_ai2_extended_diagnostics(source, max_quote_age_seconds=max_quote_age_seconds)
    coverage, missing = _group_coverage(source)
    run_stamp = stamp or timestamp()
    out_dir = Path(output_dir) if output_dir else base / "data" / "trading" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_path = out_dir / f"ai2_extended_enrichment_{run_stamp}.csv"
    summary_path = out_dir / f"ai2_extended_enrichment_{run_stamp}.md"
    detail.to_csv(detail_path, index=False)
    _write_summary(summary_path, source_path=source_path, frame=detail, coverage=coverage, missing=missing)
    return AI2ExtendedEnrichmentDiagnosticResult(
        status="ok" if source_path is not None and not source.empty else "missing_data",
        rows=len(detail),
        source_path=source_path,
        detail_path=detail_path,
        summary_path=summary_path,
        group_coverage=coverage,
        missing_columns=missing,
    )


def result_to_dict(result: AI2ExtendedEnrichmentDiagnosticResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "rows": result.rows,
        "source_path": str(result.source_path or ""),
        "detail_path": str(result.detail_path),
        "summary_path": str(result.summary_path),
        "group_coverage": result.group_coverage,
        "missing_columns": result.missing_columns,
    }
