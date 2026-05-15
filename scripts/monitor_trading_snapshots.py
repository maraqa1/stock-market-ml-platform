#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request

import pandas as pd


PASS = "PASS"
WARN = "WARN"
ALERT = "ALERT"


COLORS = {
    PASS: "\033[92m",
    WARN: "\033[93m",
    ALERT: "\033[91m",
    "END": "\033[0m",
}


@dataclass
class MonitorResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([""] * len(frame), index=frame.index, dtype="object")
    return frame[column].fillna("").astype(str)


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([pd.NA] * len(frame), index=frame.index, dtype="Float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _keyed(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    for column in ["pool", "symbol", "direction"]:
        work[column] = _text(work, column).str.lower() if column != "symbol" else _text(work, column).str.upper()
    work["__key"] = work["pool"] + "|" + work["symbol"] + "|" + work["direction"]
    return work.set_index("__key", drop=False)


def _parse_json(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class SnapshotComparator:
    def __init__(self, previous: pd.DataFrame, current: pd.DataFrame, *, score_tolerance: float = 0.001):
        self.previous = _keyed(previous)
        self.current = _keyed(current)
        self.score_tolerance = score_tolerance
        self.common_index = self.previous.index.intersection(self.current.index)

    def field_change_counts(self) -> dict[str, int]:
        return {
            "outcomes": self._text_changes("outcome"),
            "scores": self._numeric_changes("raw_score") + self._numeric_changes("display_score"),
            "notional": self._numeric_changes("notional"),
            "quantity": self._numeric_changes("quantity"),
        }

    def _text_changes(self, column: str) -> int:
        if self.common_index.empty:
            return 0
        left = _text(self.previous.loc[self.common_index], column)
        right = _text(self.current.loc[self.common_index], column)
        return int((left != right).sum())

    def _numeric_changes(self, column: str) -> int:
        if self.common_index.empty:
            return 0
        left = _num(self.previous.loc[self.common_index], column)
        right = _num(self.current.loc[self.common_index], column)
        both_null = left.isna() & right.isna()
        changed = (left - right).abs().gt(self.score_tolerance)
        changed = changed | (left.isna() ^ right.isna())
        return int((changed & ~both_null).sum())


class AlertManager:
    def __init__(self, log_path: Path, *, slack_webhook: str | None = None):
        self.log_path = log_path
        self.slack_webhook = slack_webhook
        self.fired_keys: set[str] = set()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, result: MonitorResult, *, snapshot_path: Path) -> None:
        color = COLORS.get(result.status, "")
        end = COLORS["END"] if color else ""
        print(f"{color}[{result.status}] {result.name}: {result.message}{end}")
        record = {
            "logged_at": _now_iso(),
            "snapshot": str(snapshot_path),
            "check": result.name,
            "status": result.status,
            "message": result.message,
            "details": result.details,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
        if result.status == ALERT:
            self._send_alert_once(result)

    def _send_alert_once(self, result: MonitorResult) -> None:
        key = f"{result.name}:{result.message}"
        if key in self.fired_keys:
            return
        self.fired_keys.add(key)
        if not self.slack_webhook:
            return
        payload = json.dumps({"text": f"[{result.status}] {result.name}: {result.message}"}).encode("utf-8")
        req = request.Request(self.slack_webhook, data=payload, headers={"Content-Type": "application/json"})
        try:
            request.urlopen(req, timeout=10).read()
        except Exception as exc:
            print(f"{COLORS[WARN]}[WARN] slack_post: {exc}{COLORS['END']}")


class TradingSnapshotMonitor:
    def __init__(
        self,
        directory: Path,
        *,
        stale_threshold_seconds: int = 3600,
        freeze_cycles: int = 2,
        sizing_block_cycles: int = 2,
        meta_label_cycles: int = 3,
        state_path: Path | None = None,
        log_path: Path | None = None,
        slack_webhook: str | None = None,
    ):
        self.directory = directory
        self.stale_threshold_seconds = stale_threshold_seconds
        self.freeze_cycles = freeze_cycles
        self.sizing_block_cycles = sizing_block_cycles
        self.meta_label_cycles = meta_label_cycles
        self.state_path = state_path or directory / "monitor_state.json"
        self.alerts = AlertManager(log_path or directory / "monitor.log", slack_webhook=slack_webhook)
        self.state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"processed": [], "freeze_streak": 0, "accepted_unsized": {}, "meta_label_low_streak": 0, "last_stale_count": None}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {"processed": [], "freeze_streak": 0, "accepted_unsized": {}, "meta_label_low_streak": 0, "last_stale_count": None}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8")

    def latest_files(self) -> list[Path]:
        return sorted(self.directory.glob("trading_snapshot_*.csv"), key=lambda path: path.stat().st_mtime)

    def process_new_files(self) -> list[MonitorResult]:
        processed = set(self.state.get("processed", []))
        results: list[MonitorResult] = []
        for path in self.latest_files():
            if str(path) in processed:
                continue
            results.extend(self.process_snapshot(path))
            processed.add(str(path))
        self.state["processed"] = sorted(processed)[-100:]
        self._save_state()
        return results

    def process_snapshot(self, path: Path) -> list[MonitorResult]:
        current = pd.read_csv(path)
        previous_path = self.state.get("last_snapshot")
        previous = pd.read_csv(previous_path) if previous_path and Path(previous_path).exists() else None
        results = run_snapshot_checks(
            current,
            previous,
            state=self.state,
            stale_threshold_seconds=self.stale_threshold_seconds,
            freeze_cycles=self.freeze_cycles,
            sizing_block_cycles=self.sizing_block_cycles,
            meta_label_cycles=self.meta_label_cycles,
        )
        for result in results:
            self.alerts.emit(result, snapshot_path=path)
        self.state["last_snapshot"] = str(path)
        self._save_state()
        return results


def check_pipeline_freeze(current: pd.DataFrame, previous: pd.DataFrame | None, state: dict[str, Any], *, freeze_cycles: int) -> MonitorResult:
    if previous is None:
        state["freeze_streak"] = 0
        return MonitorResult("pipeline_freeze", PASS, "No previous snapshot yet; freeze check initialized.")
    counts = SnapshotComparator(previous, current).field_change_counts()
    frozen = counts["outcomes"] == 0 and counts["scores"] == 0
    state["freeze_streak"] = int(state.get("freeze_streak", 0)) + 1 if frozen else 0
    status = ALERT if state["freeze_streak"] >= freeze_cycles else (WARN if frozen else PASS)
    message = "Pipeline frozen" if status == ALERT else ("No outcome/score changes this cycle" if frozen else "Pipeline changed this cycle.")
    return MonitorResult("pipeline_freeze", status, message, {"freeze_streak": state["freeze_streak"], "change_counts": counts})


def check_sizing_blockage(current: pd.DataFrame, state: dict[str, Any], *, sizing_block_cycles: int) -> MonitorResult:
    outcome = _text(current, "outcome").str.lower()
    notional_missing = _num(current, "notional").isna()
    quantity_missing = _num(current, "quantity").isna()
    accepted_unsized = current[outcome.eq("accepted") & (notional_missing | quantity_missing)]
    symbols = sorted(set(_text(accepted_unsized, "symbol").str.upper()) - {""})
    counters = dict(state.get("accepted_unsized", {}))
    next_counters: dict[str, int] = {}
    for symbol in symbols:
        next_counters[symbol] = int(counters.get(symbol, 0)) + 1
    state["accepted_unsized"] = next_counters
    stuck = sorted(symbol for symbol, count in next_counters.items() if count >= sizing_block_cycles)
    if stuck:
        return MonitorResult("sizing_blockage", ALERT, f"Accepted candidates stuck unsized for {sizing_block_cycles}+ snapshots: {', '.join(stuck[:20])}", {"symbols": stuck, "cycles": next_counters})
    if symbols:
        return MonitorResult("sizing_blockage", WARN, f"{len(symbols)} accepted candidates are unsized in this snapshot.", {"symbols": symbols})
    return MonitorResult("sizing_blockage", PASS, "No accepted candidates with missing notional/quantity.")


def check_stale_data(current: pd.DataFrame, state: dict[str, Any], *, stale_threshold_seconds: int) -> MonitorResult:
    ages = _num(current, "data_age_seconds")
    stale = current[ages.gt(stale_threshold_seconds)]
    stale_count = int(len(stale))
    previous_count = state.get("last_stale_count")
    state["last_stale_count"] = stale_count
    over_4h = current[ages.gt(14400)]
    details = {"stale_count": stale_count, "previous_stale_count": previous_count, "max_age_seconds": int(ages.max()) if ages.notna().any() else None}
    if not over_4h.empty:
        symbols = sorted(set(_text(over_4h, "symbol").str.upper()) - {""})
        return MonitorResult("stale_data", ALERT, f"{len(over_4h)} rows exceed 4h data age.", {**details, "symbols": symbols[:50]})
    if previous_count is not None and previous_count > 0 and stale_count > previous_count * 1.2:
        return MonitorResult("stale_data", ALERT, f"Stale row count increased >20%: {previous_count} -> {stale_count}.", details)
    if stale_count:
        return MonitorResult("stale_data", WARN, f"{stale_count} rows exceed stale threshold {stale_threshold_seconds}s.", details)
    return MonitorResult("stale_data", PASS, "No stale rows above threshold.", details)


def check_meta_label_suppression(current: pd.DataFrame, state: dict[str, Any], *, meta_label_cycles: int) -> MonitorResult:
    values: list[float] = []
    for raw in _text(current, "raw_json"):
        payload = _parse_json(raw)
        value = payload.get("meta_label_probability")
        if value in [None, ""]:
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        state["meta_label_low_streak"] = 0
        return MonitorResult("meta_label_suppression", PASS, "No meta_label_probability values found; suppression check skipped.")
    mean_value = sum(values) / len(values)
    low = mean_value < 0.45
    state["meta_label_low_streak"] = int(state.get("meta_label_low_streak", 0)) + 1 if low else 0
    details = {"mean_meta_label_probability": round(mean_value, 6), "rows": len(values), "low_streak": state["meta_label_low_streak"]}
    if state["meta_label_low_streak"] >= meta_label_cycles:
        return MonitorResult("meta_label_suppression", ALERT, "Meta-label suppressing all trades", details)
    if low:
        return MonitorResult("meta_label_suppression", WARN, f"Mean meta-label probability is low: {mean_value:.3f}.", details)
    return MonitorResult("meta_label_suppression", PASS, f"Mean meta-label probability is {mean_value:.3f}.", details)


def run_snapshot_checks(
    current: pd.DataFrame,
    previous: pd.DataFrame | None,
    *,
    state: dict[str, Any],
    stale_threshold_seconds: int = 3600,
    freeze_cycles: int = 2,
    sizing_block_cycles: int = 2,
    meta_label_cycles: int = 3,
) -> list[MonitorResult]:
    return [
        check_pipeline_freeze(current, previous, state, freeze_cycles=freeze_cycles),
        check_sizing_blockage(current, state, sizing_block_cycles=sizing_block_cycles),
        check_stale_data(current, state, stale_threshold_seconds=stale_threshold_seconds),
        check_meta_label_suppression(current, state, meta_label_cycles=meta_label_cycles),
    ]


def watch_dir(path: Path, *, interval: int = 60, **kwargs: Any) -> None:
    monitor = TradingSnapshotMonitor(path, **kwargs)
    print(f"Watching {path} for trading snapshots every {interval}s")
    while True:
        monitor.process_new_files()
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor StockML trading snapshot CSVs for pipeline anomalies.")
    parser.add_argument("snapshot_dir", type=Path, help="Directory containing trading_snapshot_*.csv files.")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds for --watch.")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new snapshots. Without this, process new files once.")
    parser.add_argument("--state-path", type=Path, default=None, help="Path to monitor_state.json.")
    parser.add_argument("--log-path", type=Path, default=None, help="Path to monitor.log JSONL output.")
    parser.add_argument("--stale-threshold-seconds", type=int, default=3600)
    parser.add_argument("--freeze-cycles", type=int, default=2)
    parser.add_argument("--sizing-block-cycles", type=int, default=2)
    parser.add_argument("--meta-label-cycles", type=int, default=3)
    parser.add_argument("--slack-webhook", default=None)
    args = parser.parse_args(argv)

    kwargs = {
        "stale_threshold_seconds": args.stale_threshold_seconds,
        "freeze_cycles": args.freeze_cycles,
        "sizing_block_cycles": args.sizing_block_cycles,
        "meta_label_cycles": args.meta_label_cycles,
        "state_path": args.state_path,
        "log_path": args.log_path,
        "slack_webhook": args.slack_webhook,
    }
    if args.watch:
        watch_dir(args.snapshot_dir, interval=args.interval, **kwargs)
        return 0
    monitor = TradingSnapshotMonitor(args.snapshot_dir, **kwargs)
    results = monitor.process_new_files()
    return 1 if any(result.status == ALERT for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
