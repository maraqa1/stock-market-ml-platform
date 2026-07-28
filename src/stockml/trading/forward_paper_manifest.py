from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import GOLD_DIR, MODEL_OUTPUTS_DIR, PIPELINE_RUNS_DIR, PORTAL_OUTPUTS_DIR, PROJECT_ROOT, TRADING_DIR, ensure_data_dirs, latest_file
from stockml.trading.config import alpaca_config
from stockml.trading.config_fingerprint import config_fingerprints, fingerprint_json


FORWARD_PAPER_DIR = TRADING_DIR / "forward_paper"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest(directory: Path, pattern: str) -> str:
    path = latest_file(directory, pattern)
    return str(path) if path else ""


def _sha256_file(path: str | Path | None) -> str:
    if not path:
        return ""
    source = Path(path)
    if not source.exists() or not source.is_file():
        return ""
    h = hashlib.sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""
    return result.stdout.strip()


def _latest_pipeline_manifest(root: Path) -> str:
    path = latest_file(root / "data" / "pipeline_runs", "*/manifest.json")
    return str(path) if path else ""


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _previous_manifest(output_dir: Path, current_path: Path) -> dict[str, str]:
    files = sorted(output_dir.glob("forward_paper_manifest_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        if path == current_path:
            continue
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            return rows[0]
    return {}


def _read_csv(path: str | Path | None) -> pd.DataFrame:
    if not path:
        return pd.DataFrame()
    source = Path(path)
    if not source.exists() or not source.is_file() or source.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(source, low_memory=False)
    except Exception:
        return pd.DataFrame()


def _boolish(value: Any) -> bool:
    if value in [None, ""]:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _execution_metrics(candidate_path: str, order_results_path: str) -> dict[str, str]:
    candidates = _read_csv(candidate_path)
    results = _read_csv(order_results_path)
    executable = 0
    if not candidates.empty:
        final_side = candidates.get("final_execution_side", pd.Series("", index=candidates.index)).fillna("").astype(str).str.upper()
        executable_flag = candidates.get("executable", pd.Series(False, index=candidates.index)).map(_boolish)
        status = candidates.get("status", pd.Series("", index=candidates.index)).fillna("").astype(str).str.lower()
        executable = int(((final_side.isin(["LONG", "SHORT"])) | executable_flag | status.eq("executable")).sum())
    submitted = 0
    filled = 0
    not_submitted_reason = ""
    if not results.empty:
        status = results.get("status", pd.Series("", index=results.index)).fillna("").astype(str).str.lower()
        alpaca = results.get("alpaca_status", pd.Series("", index=results.index)).fillna("").astype(str).str.lower()
        submitted = int((status.isin(["submitted"]) | alpaca.isin(["new", "accepted", "partially_filled", "filled"])).sum())
        filled = int(alpaca.eq("filled").sum())
        if executable > submitted:
            messages = results.get("message", pd.Series("", index=results.index)).fillna("").astype(str)
            not_submitted_reason = "; ".join([f"{k}:{v}" for k, v in messages.value_counts().head(5).items() if k])
    submitted_ratio = (submitted / executable) if executable else 0.0
    filled_ratio = (filled / submitted) if submitted else 0.0
    return {
        "executable_candidate_count": str(executable),
        "submitted_order_count": str(submitted),
        "filled_order_count": str(filled),
        "submitted_to_executable_ratio": f"{submitted_ratio:.6f}",
        "filled_to_submitted_ratio": f"{filled_ratio:.6f}",
        "executable_not_submitted_count": str(max(executable - submitted, 0)),
        "executable_not_submitted_reasons": not_submitted_reason,
    }


def build_forward_paper_manifest(
    *,
    root: Path | None = None,
    pipeline_manifest_path: str | Path | None = None,
    pipeline_run_id: str | None = None,
    run_date: str | None = None,
) -> dict[str, str]:
    base = root or PROJECT_ROOT
    fps = config_fingerprints(root=base)
    pipeline_manifest = str(pipeline_manifest_path or _latest_pipeline_manifest(base))
    pipeline_data = _read_json(pipeline_manifest) if pipeline_manifest else {}
    model_path = _latest(base / "data" / "model_outputs", "advanced_model_signal_table_*.csv")
    candidate_path = _latest(base / "data" / "portal_outputs", "08_alpaca_paper_candidate_pool_*.csv")
    execution_ranked_path = _latest(base / "data" / "portal_outputs", "execution_ranked_candidates_*.csv")
    order_plan_path = _latest(base / "data" / "portal_outputs", "08_alpaca_paper_order_plan_*.csv")
    order_results_path = _latest(base / "data" / "portal_outputs", "08_alpaca_paper_order_results_*.csv")
    tracking_path = _latest(base / "data" / "portal_outputs", "08_alpaca_paper_order_tracking_*.csv")
    positions_path = _latest(base / "data" / "portal_outputs", "08_alpaca_paper_positions_*.csv")
    cfg = alpaca_config()
    execution_metrics = _execution_metrics(candidate_path, order_results_path)
    return {
        "manifest_created_at": _now(),
        "run_date": run_date or datetime.now(timezone.utc).strftime("%Y%m%d"),
        "pipeline_run_id": pipeline_run_id or str(pipeline_data.get("run_id") or ""),
        "pipeline_manifest": pipeline_manifest,
        "pipeline_status": str(pipeline_data.get("status") or ""),
        "code_commit": _git_commit(base),
        "strategy_config_hash": fps["strategy"].digest,
        "gate_config_hash": fps["gate"].digest,
        "config_hash": fps["config"].digest,
        "config_fingerprint_json": fingerprint_json(fps),
        "config_missing_files": ";".join(sorted({item for fp in fps.values() for item in fp.missing_files})),
        "gold_dataset_id": _latest(GOLD_DIR if base == PROJECT_ROOT else base / "data" / "gold", "gold_stock_decision_daily_*.csv")
        or _latest(GOLD_DIR if base == PROJECT_ROOT else base / "data" / "gold", "06_us_gold_ml_dataset_*.csv"),
        "model_version": "",
        "model_artifact_path": model_path,
        "model_artifact_hash": _sha256_file(model_path),
        "candidate_pool_path": candidate_path,
        "execution_ranked_candidate_path": execution_ranked_path,
        "order_plan_path": order_plan_path,
        "order_results_path": order_results_path,
        "order_tracking_path": tracking_path,
        "positions_path": positions_path,
        **execution_metrics,
        "activity_journal_export_path": _latest(base / "data" / "trading" / "exports", "activity_journal_*.csv"),
        "trade_ledger_path": _latest(base / "data" / "trading" / "diagnostics", "trade_ledger_*.csv"),
        "profitability_attribution_path": _latest(base / "data" / "trading" / "diagnostics", "profitability_attribution_*.csv"),
        "broker_fill_reconciliation_path": _latest(base / "data" / "trading" / "diagnostics", "broker_fill_reconciliation_*.csv"),
        "paper_trading_enabled": str(bool(cfg.paper_trading_enabled)),
        "live_trading_enabled": str(bool(cfg.live_trading_enabled)),
        "allow_short_selling": str(bool(cfg.allow_short_selling)),
        "paper_program_status": "not_fit_for_review" if bool(cfg.live_trading_enabled) else "running_clean",
        "material_change_flag": "false",
        "material_change_reason": "",
    }


def write_forward_paper_manifest(
    *,
    root: Path | None = None,
    output_dir: Path | None = None,
    pipeline_manifest_path: str | Path | None = None,
    pipeline_run_id: str | None = None,
    run_date: str | None = None,
) -> dict[str, str]:
    base = root or PROJECT_ROOT
    ensure_data_dirs()
    out_dir = output_dir or (base / "data" / "trading" / "forward_paper")
    out_dir.mkdir(parents=True, exist_ok=True)
    row = build_forward_paper_manifest(
        root=base,
        pipeline_manifest_path=pipeline_manifest_path,
        pipeline_run_id=pipeline_run_id,
        run_date=run_date,
    )
    path = out_dir / f"forward_paper_manifest_{row['run_date']}.csv"
    previous = _previous_manifest(out_dir, path)
    if previous and previous.get("config_hash") and previous.get("config_hash") != row["config_hash"]:
        row["material_change_flag"] = "true"
        row["material_change_reason"] = "config_hash_changed"
        row["paper_program_status"] = "segmented_by_material_change"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    row["path"] = str(path)
    return row
