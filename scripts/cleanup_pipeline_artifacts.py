from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class RetentionPattern:
    directory: str
    pattern: str
    keep: int
    recursive: bool = False
    family_retention: bool = False


DEFAULT_PATTERNS = [
    RetentionPattern("data/raw", "03_us_price_history_delta_*.csv", 3),
    RetentionPattern("data/raw", "03_us_price_history_full_*.csv", 2),
    RetentionPattern("data/raw", "01_us_equity_universe_*.csv", 5),
    RetentionPattern("data/interim", "02_us_universe_cleaned_*.csv", 5),
    RetentionPattern("data/interim", "02_us_tradable_universe_*.csv", 5),
    RetentionPattern("data/interim", "02_us_universe_summary_*.csv", 5),
    RetentionPattern("data/interim", "03_us_price_history_quality_*.csv", 5),
    RetentionPattern("data/interim", "03_us_price_validated_universe_*.csv", 5),
    RetentionPattern("data/interim", "03_us_price_download_failures_*.csv", 10),
    RetentionPattern("data/interim", "04_us_metadata_enriched_*.csv", 5),
    RetentionPattern("data/interim", "04_us_metadata_quality_*.csv", 5),
    RetentionPattern("data/interim", "05_us_feature_quality_*.csv", 5),
    RetentionPattern("data/interim", "06_us_gold_quality_*.csv", 5),
    RetentionPattern("data/interim", "06_us_gold_data_dictionary_*.csv", 5),
    RetentionPattern("data/interim", "00_symbol_coverage_audit_*.csv", 10),
    RetentionPattern("data/interim", "00_candidate_funnel_summary_*.csv", 10),
    RetentionPattern("data/interim", "00_candidate_funnel_artifacts_*.csv", 10),
    RetentionPattern("data/processed", "*.csv", 3),
    RetentionPattern("data/gold", "06_us_gold_ml_dataset_*.csv", 2),
    RetentionPattern("data/model_outputs", "*.csv", 20, family_retention=True),
    RetentionPattern("data/model_outputs", "*.json", 20, family_retention=True),
    RetentionPattern("data/portal_outputs", "*.csv", 20, family_retention=True),
    RetentionPattern("data/trading", "*.csv", 10, recursive=True),
    RetentionPattern("reports", "*.csv", 10, recursive=True),
]

PROTECTED_NAMES = {
    ".gitkeep",
    "03_us_price_history_store.csv",
    "05_news_sentiment_store.csv",
    "model_predictions_latest.csv",
    "validation_leaderboard.csv",
    "feature_audit.csv",
    "rejected_features.csv",
    "paper_autopilot_state.json",
}

STAMP_PATTERN = re.compile(r"(20\d{6}_\d{6})")


def _artifact_sort_key(path: Path) -> tuple[str, float, str]:
    match = STAMP_PATTERN.search(path.name)
    stamp = match.group(1) if match else ""
    return stamp, path.stat().st_mtime, path.name


def _files_for(pattern: RetentionPattern, root: Path) -> list[Path]:
    directory = root / pattern.directory
    if not directory.exists():
        return []
    iterator = directory.rglob(pattern.pattern) if pattern.recursive else directory.glob(pattern.pattern)
    files = [
        path
        for path in iterator
        if path.is_file()
        and path.name not in PROTECTED_NAMES
        and not path.name.endswith("_latest.csv")
    ]
    return sorted(files, key=_artifact_sort_key, reverse=True)


def _family_key(path: Path) -> str:
    stem = STAMP_PATTERN.sub("{stamp}", path.stem)
    return f"{path.parent}:{stem}{path.suffix}"


def stale_files(patterns: Iterable[RetentionPattern], root: Path = ROOT) -> list[Path]:
    stale: dict[Path, None] = {}
    for pattern in patterns:
        files = _files_for(pattern, root)
        if pattern.family_retention:
            families: dict[str, list[Path]] = {}
            for path in files:
                families.setdefault(_family_key(path), []).append(path)
            for family_files in families.values():
                for path in family_files[max(pattern.keep, 0):]:
                    stale[path] = None
        else:
            for path in files[max(pattern.keep, 0):]:
                stale[path] = None
    return sorted(stale, key=lambda path: path.stat().st_size, reverse=True)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune old generated StockML CSV/JSON artifacts. Dry-run by default.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--execute", action="store_true", help="Actually delete stale files. Omit for dry-run.")
    parser.add_argument("--keep-raw-delta", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_RAW_DELTA", "3")))
    parser.add_argument("--keep-interim", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_INTERIM", "5")))
    parser.add_argument("--keep-processed", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_PROCESSED", "3")))
    parser.add_argument("--keep-gold", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_GOLD", "2")))
    parser.add_argument("--keep-model", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_MODEL", "20")))
    parser.add_argument("--keep-portal", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_PORTAL", "20")))
    parser.add_argument("--keep-trading", type=int, default=int(os.getenv("STOCKML_CLEANUP_KEEP_TRADING", "10")))
    args = parser.parse_args()

    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_interim if p.directory == "data/interim" else p.keep, p.recursive, p.family_retention)
        for p in DEFAULT_PATTERNS
    ]
    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_raw_delta, p.recursive, p.family_retention)
        if p.directory == "data/raw" and p.pattern == "03_us_price_history_delta_*.csv"
        else p
        for p in patterns
    ]
    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_processed, p.recursive, p.family_retention)
        if p.directory == "data/processed"
        else p
        for p in patterns
    ]
    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_gold, p.recursive, p.family_retention)
        if p.directory == "data/gold"
        else p
        for p in patterns
    ]
    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_model, p.recursive, p.family_retention)
        if p.directory == "data/model_outputs"
        else p
        for p in patterns
    ]
    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_portal, p.recursive, p.family_retention)
        if p.directory == "data/portal_outputs"
        else p
        for p in patterns
    ]
    patterns = [
        RetentionPattern(p.directory, p.pattern, args.keep_trading, p.recursive, p.family_retention)
        if p.directory == "data/trading"
        else p
        for p in patterns
    ]

    candidates = stale_files(patterns, root=args.root.resolve())
    total_bytes = sum(path.stat().st_size for path in candidates)
    mode = "DELETE" if args.execute else "DRY-RUN"
    print(f"cleanup_mode: {mode}")
    print(f"files_selected: {len(candidates)}")
    print(f"bytes_selected: {total_bytes}")
    print(f"human_selected: {_human_size(total_bytes)}")

    for path in candidates:
        size = path.stat().st_size
        print(f"{_human_size(size):>10} {path}")
        if args.execute:
            path.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
