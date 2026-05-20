from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from stockml.common.paths import AGENT_DECISIONS_DIR, PORTAL_OUTPUTS_DIR, PROJECT_ROOT, latest_file


DEFAULT_TIMER_SETTINGS = {
    "positions_refresh_seconds": 30,
    "monitor_interval_seconds": 30,
    "pipeline_refresh_seconds": 60,
}

TIMER_LIMITS = {
    "positions_refresh_seconds": (5, 300),
    "monitor_interval_seconds": (30, 3600),
    "pipeline_refresh_seconds": (30, 3600),
}


def _config_path(root: Path | None = None) -> Path:
    if root is None:
        return PORTAL_OUTPUTS_DIR / "portal_timer_settings.json"
    return Path(root) / "data" / "portal_outputs" / "portal_timer_settings.json"


def _agent_decisions_dir(root: Path | None = None) -> Path:
    if root is None:
        return AGENT_DECISIONS_DIR
    return Path(root) / "data" / "trading" / "agent_decisions"


def _clean_seconds(key: str, value: Any) -> int:
    low, high = TIMER_LIMITS[key]
    try:
        parsed = int(float(value))
    except Exception:
        parsed = int(DEFAULT_TIMER_SETTINGS[key])
    return max(low, min(high, parsed))


def load_timer_settings(root: Path | None = None) -> dict[str, int]:
    settings = dict(DEFAULT_TIMER_SETTINGS)
    path = _config_path(root)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for key in settings:
                if key in data:
                    settings[key] = _clean_seconds(key, data[key])
        except Exception:
            pass
    return settings


def save_timer_settings(values: dict[str, Any], root: Path | None = None) -> dict[str, int]:
    settings = load_timer_settings(root)
    for key in settings:
        if key in values:
            settings[key] = _clean_seconds(key, values[key])
    path = _config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return settings


def seconds_label(seconds: int) -> str:
    if seconds < 60:
        return f"every {seconds}s"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"every {minutes}m"
    return f"every {seconds}s"


def timer_settings_context(root: Path | None = None) -> dict[str, Any]:
    settings = load_timer_settings(root)
    return {
        **settings,
        "positions_label": f"live ({settings['positions_refresh_seconds']}s)",
        "monitor_label": seconds_label(settings["monitor_interval_seconds"]),
        "pipeline_label": seconds_label(settings["pipeline_refresh_seconds"]),
        "config_path": str(_config_path(root if root is not None else PROJECT_ROOT)),
        "systemd_timer_note": "Systemd ticks every 30s; the monitor script skips runs until this configured interval has elapsed.",
    }


def monitor_should_run(root: Path | None = None, now: datetime | None = None) -> tuple[bool, str]:
    settings = load_timer_settings(root)
    interval = settings["monitor_interval_seconds"]
    latest = latest_file(_agent_decisions_dir(root), "position_decisions_*.csv")
    if latest is None:
        return True, "no_previous_decision_snapshot"
    current = now or datetime.now(timezone.utc)
    latest_at = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)
    age = (current - latest_at).total_seconds()
    if age >= interval:
        return True, f"latest_snapshot_age_{int(age)}s"
    return False, f"latest_snapshot_age_{int(age)}s_below_configured_{interval}s"
