#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.status == FAIL


def _has_column(frame: pd.DataFrame, column: str) -> bool:
    return column in frame.columns


def _text_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if not _has_column(frame, column):
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str)


def _numeric_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if not _has_column(frame, column):
        return pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _nullish(series: pd.Series) -> pd.Series:
    text = series.fillna("").astype(str).str.strip().str.lower()
    return series.isna() | text.isin({"", "nan", "none", "null", "<na>"})


def _symbol_list(frame: pd.DataFrame, limit: int = 20) -> str:
    symbols = sorted(set(_text_series(frame, "symbol").str.upper()) - {""})
    suffix = "" if len(symbols) <= limit else f" ... (+{len(symbols) - limit} more)"
    return ", ".join(symbols[:limit]) + suffix


def check_duplicate_symbol_direction(frame: pd.DataFrame) -> CheckResult:
    required = {"symbol", "direction"}
    if not required.issubset(frame.columns):
        return CheckResult("duplicate_symbol_direction", WARN, "Missing symbol/direction columns; duplicate check skipped.")
    work = frame.copy()
    work["symbol"] = _text_series(work, "symbol").str.upper()
    work["direction"] = _text_series(work, "direction").str.lower()
    grouped = work.groupby(["symbol", "direction"], dropna=False).agg(rows=("pool", "size"), pools=("pool", lambda values: sorted(set(map(str, values)))))
    dupes = grouped[grouped["rows"] > 1].reset_index()
    if dupes.empty:
        return CheckResult("duplicate_symbol_direction", PASS, "No repeated symbol + direction pairs across funnel rows.")
    affected = [f"{row.symbol}/{row.direction}:{int(row.rows)} rows in {','.join(row.pools)}" for row in dupes.itertuples()]
    return CheckResult(
        "duplicate_symbol_direction",
        WARN,
        f"{len(dupes)} symbol + direction pairs appear multiple times across funnel stages.",
        affected[:25],
    )


def check_ghost_rows(frame: pd.DataFrame) -> CheckResult:
    raw_null = _nullish(_numeric_series(frame, "raw_score"))
    outcome_null = _nullish(_text_series(frame, "outcome"))
    ghosts = frame[raw_null & outcome_null]
    if ghosts.empty:
        return CheckResult("ghost_rows", PASS, "No rows have both raw_score and outcome empty.")
    return CheckResult(
        "ghost_rows",
        FAIL,
        f"{len(ghosts)} rows have both raw_score and outcome empty.",
        [f"symbols: {_symbol_list(ghosts)}"],
    )


def check_short_scores_and_ranks(frame: pd.DataFrame) -> CheckResult:
    direction = _text_series(frame, "direction").str.lower()
    shorts = frame[direction.eq("short")].copy()
    if shorts.empty:
        return CheckResult("short_scores_and_ranks", WARN, "No short candidates found in snapshot.")
    raw = _numeric_series(shorts, "raw_score")
    negative = shorts[raw < 0]
    rank = _numeric_series(shorts, "rank")
    unranked = shorts[_nullish(rank)]
    details: list[str] = []
    if not negative.empty:
        details.append(f"negative raw_score shorts: {_symbol_list(negative)}")
    if not unranked.empty:
        details.append(f"unranked shorts: {_symbol_list(unranked)}")
    if details:
        return CheckResult("short_scores_and_ranks", WARN, f"{len(details)} short-side score/rank warnings found.", details)
    return CheckResult("short_scores_and_ranks", PASS, f"{len(shorts)} short rows found; ranks present and raw_score is non-negative where provided.")


def check_score_raw_score_mismatch(frame: pd.DataFrame, tolerance: float = 0.0001) -> CheckResult:
    raw = _numeric_series(frame, "raw_score")
    score = _numeric_series(frame, "score")
    comparable = raw.notna() & score.notna()
    diff = score - raw
    mismatches = frame[comparable & diff.abs().gt(tolerance)].copy()
    if mismatches.empty:
        return CheckResult("score_raw_score_mismatch", PASS, f"No score/raw_score mismatches above tolerance {tolerance}.")
    mismatches["offset"] = diff.loc[mismatches.index].round(6)
    mismatches["direction"] = _text_series(mismatches, "direction").str.lower()
    mismatches["outcome"] = _text_series(mismatches, "outcome").str.lower()
    grouped = (
        mismatches.groupby(["direction", "outcome", "offset"], dropna=False)
        .size()
        .reset_index(name="rows")
        .sort_values(["direction", "outcome", "offset"])
    )
    details = [f"{row.direction or 'blank'}/{row.outcome or 'blank'} diff={row.offset}: {int(row.rows)} rows" for row in grouped.itertuples()]
    return CheckResult("score_raw_score_mismatch", WARN, f"{len(mismatches)} rows have score != raw_score.", details[:30])


def _suggest_outcome(stage: str) -> str:
    mapping = {
        "scored": "scored",
        "quality_gated": "pending",
        "meta_filtered": "pending",
        "sized": "pending",
        "selected": "accepted",
        "submitted": "pending",
        "filled": "accepted",
        "rejected": "rejected",
        "near_miss": "near_miss",
    }
    return mapping.get(stage, "pending")


def check_null_outcome(frame: pd.DataFrame) -> CheckResult:
    outcome_null = _nullish(_text_series(frame, "outcome"))
    stage = _text_series(frame, "funnel_stage").str.lower()
    stage_present = ~_nullish(stage)
    affected = frame[outcome_null & stage_present].copy()
    if affected.empty:
        return CheckResult("null_outcome", PASS, "Every row with funnel_stage has an outcome.")
    affected["stage_name"] = stage.loc[affected.index]
    grouped = affected.groupby("stage_name", dropna=False).size().reset_index(name="rows")
    details = [f"{row.stage_name}: {int(row.rows)} rows; suggested outcome='{_suggest_outcome(row.stage_name)}'" for row in grouped.itertuples()]
    return CheckResult("null_outcome", WARN, f"{len(affected)} rows have null outcome while funnel_stage is populated.", details)


def check_exact_duplicate_near_miss(frame: pd.DataFrame) -> CheckResult:
    if not {"pool", "symbol", "direction", "outcome", "raw_score"}.issubset(frame.columns):
        return CheckResult("exact_duplicate_near_miss", WARN, "Missing near-miss duplicate check columns; check skipped.")
    near = frame[_text_series(frame, "pool").str.lower().eq("near_miss")].copy()
    if near.empty:
        return CheckResult("exact_duplicate_near_miss", PASS, "No near_miss rows present.")
    near["symbol"] = _text_series(near, "symbol").str.upper()
    near["direction"] = _text_series(near, "direction").str.lower()
    near["outcome"] = _text_series(near, "outcome").str.lower()
    near["raw_score"] = _numeric_series(near, "raw_score").round(8)
    grouped = near.groupby(["symbol", "direction", "outcome", "raw_score"], dropna=False).size().reset_index(name="rows")
    dupes = grouped[grouped["rows"] > 1]
    if dupes.empty:
        return CheckResult("exact_duplicate_near_miss", PASS, "No exact duplicate near_miss rows.")
    details = [f"{row.symbol}/{row.direction}/{row.outcome}/raw_score={row.raw_score}: {int(row.rows)} rows" for row in dupes.itertuples()]
    return CheckResult("exact_duplicate_near_miss", FAIL, f"{len(dupes)} exact duplicate near_miss keys found.", details)


def check_stale_data(frame: pd.DataFrame, threshold_seconds: int = 3600) -> CheckResult:
    ages = _numeric_series(frame, "data_age_seconds")
    stale = frame[ages.gt(threshold_seconds)].copy()
    if stale.empty:
        return CheckResult("stale_data", PASS, f"No rows exceed data_age_seconds threshold {threshold_seconds}.")
    stale["__age"] = ages.loc[stale.index].astype("Int64")
    grouped = (
        stale.groupby(["pool", "symbol"], dropna=False)["__age"]
        .max()
        .reset_index(name="age_seconds")
        .sort_values("age_seconds", ascending=False)
    )
    details = [f"{row.pool}/{row.symbol}: {int(row.age_seconds)}s" for row in grouped.itertuples()]
    return CheckResult("stale_data", WARN, f"{len(stale)} rows exceed data_age_seconds threshold {threshold_seconds}.", details[:30])


CHECKS: list[Callable[[pd.DataFrame], CheckResult]] = [
    check_duplicate_symbol_direction,
    check_ghost_rows,
    check_short_scores_and_ranks,
    check_score_raw_score_mismatch,
    check_null_outcome,
    check_exact_duplicate_near_miss,
]


def run_checks(frame: pd.DataFrame, *, stale_threshold_seconds: int = 3600) -> list[CheckResult]:
    results = [check(frame) for check in CHECKS]
    results.append(check_stale_data(frame, threshold_seconds=stale_threshold_seconds))
    return results


def print_report(results: list[CheckResult], *, path: Path) -> None:
    print(f"Trading snapshot data quality report: {path}")
    print("=" * 80)
    for result in results:
        print(f"[{result.status}] {result.name}: {result.message}")
        for detail in result.details:
            print(f"  - {detail}")
    print("=" * 80)
    failures = sum(1 for result in results if result.status == FAIL)
    warnings = sum(1 for result in results if result.status == WARN)
    print(f"Summary: {failures} FAIL, {warnings} WARN, {len(results) - failures - warnings} PASS")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a StockML trading snapshot CSV.")
    parser.add_argument("snapshot_csv", type=Path, help="Path to trading_snapshot_*.csv")
    parser.add_argument("--stale-threshold-seconds", type=int, default=3600, help="Warn when data_age_seconds exceeds this value.")
    args = parser.parse_args(argv)

    frame = pd.read_csv(args.snapshot_csv)
    results = run_checks(frame, stale_threshold_seconds=args.stale_threshold_seconds)
    print_report(results, path=args.snapshot_csv)
    return 1 if any(result.failed for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
