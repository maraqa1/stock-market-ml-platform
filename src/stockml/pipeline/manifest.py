from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from stockml.common.paths import PIPELINE_RUNS_DIR, ensure_data_dirs, timestamp


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _serialise(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _serialise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialise(item) for item in value]
    return value


class PipelineManifest:
    def __init__(self, profile_name: str, *, run_id: str | None = None) -> None:
        ensure_data_dirs()
        self.run_id = run_id or timestamp()
        self.path = PIPELINE_RUNS_DIR / self.run_id / "manifest.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, Any] = {
            "run_id": self.run_id,
            "profile": profile_name,
            "status": "running",
            "started_at": _now(),
            "finished_at": "",
            "stages": {},
        }
        self.write()

    def write(self) -> None:
        self.path.write_text(json.dumps(_serialise(self.data), indent=2, sort_keys=True), encoding="utf-8")

    def stage_ok(self, name: str, outputs: dict[str, Any] | None = None) -> None:
        self.data["stages"][name] = {
            "status": "ok",
            "finished_at": _now(),
            "outputs": outputs or {},
        }
        self.write()

    def stage_failed(self, name: str, exc: BaseException) -> None:
        self.data["status"] = "failed"
        self.data["failed_stage"] = name
        self.data["finished_at"] = _now()
        self.data["stages"][name] = {
            "status": "failed",
            "finished_at": _now(),
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        self.write()

    def complete(self) -> None:
        self.data["status"] = "ok"
        self.data["finished_at"] = _now()
        self.write()
