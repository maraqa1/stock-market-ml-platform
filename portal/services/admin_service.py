from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from portal.services.latest_file_reader import latest_file, safe_read_csv


ADMIN_ACTIONS: dict[str, dict[str, str]] = {
    "quality": {
        "label": "Run Quality Check",
        "description": "Validate universe, metadata, Gold, model freshness, and market-cap coverage.",
    },
    "readiness": {
        "label": "Full Readiness Repair",
        "description": "Run the us_full profile, quality gate, plan-only trader, and holding review.",
    },
    "readiness-current": {
        "label": "Plan From Current Artifacts",
        "description": "Run quality first, then create plan-only outputs and holding review from current artifacts.",
    },
    "metadata": {
        "label": "Rebuild Metadata",
        "description": "Rebuild metadata with EODHD plus Yahoo fallback and stop if coverage is poor.",
    },
    "stop-intraday": {
        "label": "Stop Intraday Clock",
        "description": "Stop the intraday trading clock so broken artifacts do not keep regenerating.",
    },
}


def _admin_dir(root: Path) -> Path:
    return root / "data" / "portal_outputs" / "admin_jobs"


def _job_path(root: Path, job_id: str) -> Path:
    return _admin_dir(root) / f"{job_id}.json"


def _log_path(root: Path, job_id: str) -> Path:
    return _admin_dir(root) / f"{job_id}.log"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _python_env(root: Path) -> dict[str, str]:
    env = os.environ.copy()
    src = str(root / "src")
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not current else f"{src}{os.pathsep}{current}"
    return env


def _command_for(action: str, root: Path) -> list[str]:
    python = sys.executable
    if action == "quality":
        return [python, str(root / "scripts" / "run_pipeline_quality_checks.py")]
    if action == "readiness":
        return [python, str(root / "scripts" / "run_trading_day_readiness.py"), "--profile", "us_full"]
    if action == "readiness-current":
        return [python, str(root / "scripts" / "run_trading_day_readiness.py"), "--skip-profile"]
    if action == "metadata":
        return [
            python,
            str(root / "scripts" / "run_metadata_pipeline.py"),
            "--provider",
            "eodhd",
            "--fallback-provider",
            "yahoo_legacy",
            "--exchange",
            "NYSE,NASDAQ",
        ]
    if action == "stop-intraday":
        return ["pkill", "-f", "scripts/run_intraday_trading_clock.py"]
    raise KeyError(action)


def _write_job(root: Path, job: dict[str, Any]) -> None:
    path = _job_path(root, str(job["job_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(job, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_job(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def latest_jobs(root: Path, limit: int = 8) -> list[dict[str, Any]]:
    directory = _admin_dir(root)
    if not directory.exists():
        return []
    files = sorted(directory.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    jobs = []
    for path in files[:limit]:
        job = _read_job(path)
        if job:
            jobs.append(job)
    return jobs


def run_admin_action(root: Path, action: str) -> dict[str, Any]:
    if action not in ADMIN_ACTIONS:
        return {"status": "rejected", "message": "unsupported_admin_action", "action": action}
    job_id = f"{_stamp()}_{action.replace('-', '_')}"
    log_path = _log_path(root, job_id)
    command = _command_for(action, root)
    job = {
        "job_id": job_id,
        "action": action,
        "label": ADMIN_ACTIONS[action]["label"],
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "command": " ".join(command),
        "log_path": str(log_path),
        "pid": "",
    }
    _write_job(root, job)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env=_python_env(root),
            start_new_session=True,
        )
        job["pid"] = process.pid
        _write_job(root, job)
        handle.close()
    except Exception as exc:
        handle.close()
        job.update({"status": "failed_to_start", "error": str(exc), "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        _write_job(root, job)
    return job


def latest_log_tail(root: Path, job: dict[str, Any] | None = None, max_lines: int = 80) -> str:
    selected = job or (latest_jobs(root, limit=1)[0] if latest_jobs(root, limit=1) else None)
    if not selected:
        return ""
    path = Path(str(selected.get("log_path") or ""))
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    return "\n".join(lines[-max_lines:])


def quality_rows(root: Path) -> list[dict[str, Any]]:
    path = latest_file(root, "interim", "00_pipeline_quality_report_*.csv")
    frame = safe_read_csv(path)
    if frame.empty:
        return []
    return frame.fillna("").to_dict("records")


def admin_context(root: Path) -> dict[str, Any]:
    rows = quality_rows(root)
    failures = [row for row in rows if str(row.get("status") or "").lower() == "fail"]
    jobs = latest_jobs(root)
    return {
        "actions": [{"key": key, **value} for key, value in ADMIN_ACTIONS.items()],
        "quality_rows": rows,
        "quality_failures": failures,
        "quality_status": "failed" if failures else ("ok" if rows else "missing"),
        "latest_quality_path": str(latest_file(root, "interim", "00_pipeline_quality_report_*.csv") or ""),
        "jobs": jobs,
        "latest_job": jobs[0] if jobs else {},
        "latest_log_tail": latest_log_tail(root, jobs[0] if jobs else None),
    }
