from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

from stockml.common.paths import (
    GOLD_DIR,
    INTERIM_DIR,
    MODEL_OUTPUTS_DIR,
    PROCESSED_DIR,
    PROJECT_ROOT,
    ensure_data_dirs,
    latest_file,
    timestamp,
)


@dataclass(frozen=True)
class PipelineQualityThresholds:
    min_universe_rows: int = 1000
    min_validated_rows: int = 500
    min_validated_universe_coverage: float = 0.40
    min_metadata_validated_coverage: float = 0.75
    min_metadata_market_cap_coverage: float = 0.70
    min_gold_rows: int = 500_000
    min_gold_validated_coverage: float = 0.75
    max_gold_missing_market_cap_rate: float = 0.30
    max_gold_duplicate_key_rate: float = 0.0


REPORT_COLUMNS = [
    "check",
    "status",
    "observed",
    "threshold",
    "message",
]


def _manifest_path(root: Path, manifest: dict[str, object], stage: str, *keys: str) -> Path | None:
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return None
    stage_data = stages.get(stage)
    if not isinstance(stage_data, dict) or stage_data.get("status") != "ok":
        return None
    outputs = stage_data.get("outputs")
    if not isinstance(outputs, dict):
        return None
    for key in keys:
        value = outputs.get(key)
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            path = root / path
        if path.exists():
            return path
    return None


def _manifest_artifacts(root: Path, profile_name: str) -> dict[str, Path | None] | None:
    manifests = sorted(
        (root / "data" / "pipeline_runs").glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if manifest.get("status") != "ok" or manifest.get("profile") != profile_name:
            continue
        artifacts = {
            "universe": _manifest_path(root, manifest, "universe", "tradable_universe"),
            "validated": _manifest_path(root, manifest, "price", "validated_universe"),
            "metadata": _manifest_path(root, manifest, "metadata", "metadata_enriched"),
            "features": _manifest_path(root, manifest, "features", "feature_panel"),
            "gold": _manifest_path(root, manifest, "gold", "gold_dataset"),
            "model": _manifest_path(root, manifest, "model", "predictions", "signal_table", "model_predictions_latest"),
        }
        if all(artifacts.values()):
            return artifacts
    return None


def _latest_artifacts(root: Path, profile_name: str | None = "us_full") -> dict[str, Path | None]:
    if profile_name:
        artifacts = _manifest_artifacts(root, profile_name)
        if artifacts is not None:
            return artifacts
    interim = root / "data" / "interim"
    processed = root / "data" / "processed"
    gold = root / "data" / "gold"
    model = root / "data" / "model_outputs"
    return {
        "universe": latest_file(interim, "02_us_tradable_universe_*.csv"),
        "validated": latest_file(interim, "03_us_price_validated_universe_*.csv"),
        "metadata": latest_file(interim, "04_us_metadata_enriched_*.csv"),
        "features": latest_file(processed, "05_us_feature_panel_*.csv"),
        "gold": latest_file(gold, "06_us_gold_ml_dataset_*.csv"),
        "model": model / "model_predictions_latest.csv" if (model / "model_predictions_latest.csv").exists() else None,
    }


def _row(check: str, ok: bool, observed: object, threshold: object, message: str) -> dict[str, object]:
    return {
        "check": check,
        "status": "pass" if ok else "fail",
        "observed": observed,
        "threshold": threshold,
        "message": message,
    }


def _read_symbols(path: Path | None, preferred: Iterable[str]) -> set[str]:
    if path is None or not path.exists():
        return set()
    wanted = list(preferred)
    try:
        header = pd.read_csv(path, nrows=0)
    except Exception:
        return set()
    column = next((name for name in wanted if name in header.columns), None)
    if column is None:
        return set()
    symbols: set[str] = set()
    for chunk in pd.read_csv(path, usecols=[column], chunksize=200_000, dtype=str, low_memory=False):
        symbols.update(chunk[column].dropna().astype(str).str.upper().str.strip())
    symbols.discard("")
    return symbols


def _count_rows(path: Path | None) -> int:
    if path is None or not path.exists():
        return 0
    rows = 0
    for chunk in pd.read_csv(path, usecols=lambda _: False, chunksize=200_000, low_memory=False):
        rows += len(chunk)
    return rows


def _metadata_market_cap_stats(path: Path | None) -> tuple[int, int]:
    if path is None or not path.exists():
        return 0, 0
    total = 0
    has_cap = 0
    try:
        for chunk in pd.read_csv(path, usecols=lambda col: col in {"ticker", "market_cap"}, chunksize=200_000, low_memory=False):
            total += len(chunk)
            if "market_cap" in chunk.columns:
                has_cap += int(pd.to_numeric(chunk["market_cap"], errors="coerce").notna().sum())
    except Exception:
        return 0, 0
    return total, has_cap


def _gold_stats(path: Path | None) -> dict[str, object]:
    stats: dict[str, object] = {
        "rows": 0,
        "tickers": set(),
        "missing_market_cap": 0,
        "duplicate_keys": 0,
    }
    if path is None or not path.exists():
        return stats
    seen_keys: set[tuple[str, str]] = set()
    try:
        for chunk in pd.read_csv(path, usecols=lambda col: col in {"date", "ticker", "market_cap"}, chunksize=200_000, low_memory=False):
            rows = len(chunk)
            stats["rows"] = int(stats["rows"]) + rows
            if "ticker" in chunk.columns:
                ticker = chunk["ticker"].astype(str).str.upper().str.strip()
                stats["tickers"].update(ticker[ticker.ne("")].tolist())
            if "market_cap" in chunk.columns:
                stats["missing_market_cap"] = int(stats["missing_market_cap"]) + int(pd.to_numeric(chunk["market_cap"], errors="coerce").isna().sum())
            if {"date", "ticker"}.issubset(chunk.columns):
                keys = zip(chunk["ticker"].astype(str).str.upper().str.strip(), chunk["date"].astype(str).str.strip())
                for key in keys:
                    if key in seen_keys:
                        stats["duplicate_keys"] = int(stats["duplicate_keys"]) + 1
                    else:
                        seen_keys.add(key)
    except Exception:
        stats["read_error"] = True
    return stats


def _artifact_freshness_rows(artifacts: dict[str, Path | None]) -> list[dict[str, object]]:
    order = ["universe", "validated", "metadata", "features", "gold", "model"]
    rows = []
    previous_mtime: float | None = None
    for name in order:
        path = artifacts.get(name)
        exists = bool(path and path.exists())
        mtime = path.stat().st_mtime if exists else None
        ok = exists and (previous_mtime is None or (mtime is not None and mtime >= previous_mtime))
        rows.append(
            _row(
                f"artifact_{name}_exists_and_fresh",
                bool(ok),
                path.name if path else "",
                "exists and not older than upstream",
                str(path or ""),
            )
        )
        if mtime is not None:
            previous_mtime = max(previous_mtime or mtime, mtime)
    return rows


def build_pipeline_quality_report(
    root: Path | None = None,
    thresholds: PipelineQualityThresholds | None = None,
    stamp: str | None = None,
    profile_name: str | None = "us_full",
) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    ensure_data_dirs()
    cfg = thresholds or PipelineQualityThresholds()
    artifacts = _latest_artifacts(base, profile_name=profile_name)

    universe_symbols = _read_symbols(artifacts["universe"], ["symbol", "ticker", "yahoo_ticker"])
    validated_symbols = _read_symbols(artifacts["validated"], ["yahoo_ticker", "ticker", "symbol"])
    metadata_symbols = _read_symbols(artifacts["metadata"], ["ticker", "symbol", "yahoo_ticker"])
    metadata_rows, metadata_market_cap_rows = _metadata_market_cap_stats(artifacts["metadata"])
    gold = _gold_stats(artifacts["gold"])
    gold_tickers = gold["tickers"] if isinstance(gold["tickers"], set) else set()

    universe_rows = len(universe_symbols)
    validated_rows = len(validated_symbols)
    validated_coverage = validated_rows / max(universe_rows, 1)
    metadata_coverage = len(metadata_symbols & validated_symbols) / max(validated_rows, 1)
    metadata_market_cap_coverage = metadata_market_cap_rows / max(metadata_rows, 1)
    gold_coverage = len(gold_tickers & validated_symbols) / max(validated_rows, 1)
    gold_rows = int(gold["rows"])
    gold_missing_market_cap_rate = int(gold["missing_market_cap"]) / max(gold_rows, 1)
    gold_duplicate_key_rate = int(gold["duplicate_keys"]) / max(gold_rows, 1)

    rows = []
    rows.extend(_artifact_freshness_rows(artifacts))
    rows.extend(
        [
            _row("universe_row_count", universe_rows >= cfg.min_universe_rows, universe_rows, f">={cfg.min_universe_rows}", "latest tradable universe symbol count"),
            _row("validated_row_count", validated_rows >= cfg.min_validated_rows, validated_rows, f">={cfg.min_validated_rows}", "latest price-validated universe symbol count"),
            _row(
                "validated_universe_coverage",
                validated_coverage >= cfg.min_validated_universe_coverage,
                round(validated_coverage, 4),
                f">={cfg.min_validated_universe_coverage}",
                "validated symbols divided by tradable universe symbols",
            ),
            _row(
                "metadata_validated_coverage",
                metadata_coverage >= cfg.min_metadata_validated_coverage,
                round(metadata_coverage, 4),
                f">={cfg.min_metadata_validated_coverage}",
                "metadata symbols divided by validated symbols",
            ),
            _row(
                "metadata_market_cap_coverage",
                metadata_market_cap_coverage >= cfg.min_metadata_market_cap_coverage,
                round(metadata_market_cap_coverage, 4),
                f">={cfg.min_metadata_market_cap_coverage}",
                "metadata rows with usable market_cap",
            ),
            _row("gold_row_count", gold_rows >= cfg.min_gold_rows, gold_rows, f">={cfg.min_gold_rows}", "latest Gold ML dataset row count"),
            _row(
                "gold_validated_coverage",
                gold_coverage >= cfg.min_gold_validated_coverage,
                round(gold_coverage, 4),
                f">={cfg.min_gold_validated_coverage}",
                "Gold tickers divided by validated symbols",
            ),
            _row(
                "gold_missing_market_cap_rate",
                gold_missing_market_cap_rate <= cfg.max_gold_missing_market_cap_rate,
                round(gold_missing_market_cap_rate, 4),
                f"<={cfg.max_gold_missing_market_cap_rate}",
                "Gold rows missing market_cap",
            ),
            _row(
                "gold_duplicate_ticker_date_rate",
                gold_duplicate_key_rate <= cfg.max_gold_duplicate_key_rate,
                round(gold_duplicate_key_rate, 6),
                f"<={cfg.max_gold_duplicate_key_rate}",
                "duplicate ticker/date rows in Gold",
            ),
        ]
    )
    report = pd.DataFrame(rows, columns=REPORT_COLUMNS)
    run_stamp = stamp or timestamp()
    path = base / "data" / "interim" / f"00_pipeline_quality_report_{run_stamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(path, index=False)
    failed = report[report["status"].eq("fail")]
    return {
        "status": "ok" if failed.empty else "failed",
        "path": str(path),
        "checks": int(len(report)),
        "failed_checks": int(len(failed)),
        "failures": failed.to_dict("records"),
    }
