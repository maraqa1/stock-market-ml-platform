from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stockml.common.paths import DATA_DIR, PROJECT_ROOT


REQUIRED_STAGES = ("universe", "price", "metadata", "features", "gold", "model", "trading_day_readiness")


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _data_root(root: Path) -> Path:
    return DATA_DIR if DATA_DIR != PROJECT_ROOT / "data" else root / "data"


def _manifest_paths(root: Path) -> list[Path]:
    return sorted(
        (_data_root(root) / "pipeline_runs").glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _latest_manifest(root: Path, profile_name: str) -> tuple[Path | None, dict[str, Any] | None]:
    matches: list[tuple[datetime, str, Path, dict[str, Any]]] = []
    for path in _manifest_paths(root):
        manifest = _read_manifest(path)
        if manifest and manifest.get("profile") == profile_name:
            started = _parse_time(manifest.get("started_at")) or datetime.min.replace(tzinfo=timezone.utc)
            run_id = str(manifest.get("run_id") or path.parent.name)
            matches.append((started, run_id, path, manifest))
    if not matches:
        return None, None
    _, _, path, manifest = max(matches, key=lambda item: (item[0], item[1]))
    return path, manifest


def _resolve(root: Path, value: object) -> Path | None:
    if not value or isinstance(value, (bool, int, float)):
        return None
    text = str(value)
    if not any(token in text for token in ("/", "\\")) and Path(text).suffix.lower() not in {".csv", ".json", ".parquet", ".txt"}:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    if text.startswith("data/"):
        return _data_root(root) / text.removeprefix("data/")
    return root / path


def _missing_outputs(root: Path, manifest: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    stages = manifest.get("stages")
    if not isinstance(stages, dict):
        return ["manifest.stages"]
    for stage_name, stage in stages.items():
        if not isinstance(stage, dict) or stage.get("status") != "ok":
            continue
        outputs = stage.get("outputs")
        if not isinstance(outputs, dict):
            continue
        for key, value in outputs.items():
            if isinstance(value, (dict, list)) or str(value or "").startswith("warning:"):
                continue
            path = _resolve(root, value)
            if path is not None and not path.exists():
                missing.append(f"{stage_name}.{key}={path}")
    return missing


def audit_latest_pipeline(
    root: Path | None = None,
    *,
    profile_name: str = "us_full",
    stale_after_minutes: int = 90,
    required_stages: tuple[str, ...] = REQUIRED_STAGES,
) -> dict[str, Any]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    manifest_path, manifest = _latest_manifest(base, profile_name)
    if manifest_path is None or manifest is None:
        return {
            "status": "failed",
            "reason": "manifest_missing",
            "profile": profile_name,
            "manifest_path": "",
            "missing_stages": list(required_stages),
            "missing_outputs": [],
        }

    stages = manifest.get("stages") if isinstance(manifest.get("stages"), dict) else {}
    missing_stages = [stage for stage in required_stages if stage not in stages or stages.get(stage, {}).get("status") != "ok"]
    missing_outputs = _missing_outputs(base, manifest)
    manifest_status = str(manifest.get("status") or "").lower()
    started_at = _parse_time(manifest.get("started_at"))
    finished_at = _parse_time(manifest.get("finished_at"))
    now = datetime.now(timezone.utc)
    age_minutes = ((now - started_at).total_seconds() / 60.0) if started_at else 0.0

    reason = ""
    status = "ok"
    if manifest_status == "running":
        status = "running" if age_minutes < stale_after_minutes else "failed"
        reason = "pipeline_running" if status == "running" else "pipeline_stale_running"
    elif manifest_status != "ok":
        status = "failed"
        reason = f"pipeline_{manifest_status or 'unknown'}"
    elif missing_stages:
        status = "failed"
        reason = "required_stage_missing"
    elif missing_outputs:
        status = "failed"
        reason = "artifact_missing"

    return {
        "status": status,
        "reason": reason,
        "profile": profile_name,
        "manifest_path": str(manifest_path),
        "run_id": str(manifest.get("run_id") or manifest_path.parent.name),
        "manifest_status": manifest_status,
        "started_at": manifest.get("started_at", ""),
        "finished_at": manifest.get("finished_at", ""),
        "age_minutes": round(age_minutes, 2),
        "stale_after_minutes": stale_after_minutes,
        "missing_stages": missing_stages,
        "missing_outputs": missing_outputs,
        "failed_stage": manifest.get("failed_stage", ""),
        "is_complete": manifest_status == "ok" and not missing_stages and not missing_outputs and finished_at is not None,
    }
