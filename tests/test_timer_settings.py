from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import shutil

from stockml.trading.timer_settings import load_timer_settings, monitor_should_run, save_timer_settings


def temp_root() -> Path:
    root = Path(".pytest_workspace") / f"timer_settings_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_timer_settings_are_clamped_and_persisted():
    root = temp_root()
    try:
        settings = save_timer_settings(
            {
                "positions_refresh_seconds": "1",
                "monitor_interval_seconds": "45",
                "pipeline_refresh_seconds": "99999",
            },
            root,
        )
        assert settings["positions_refresh_seconds"] == 5
        assert settings["monitor_interval_seconds"] == 45
        assert settings["pipeline_refresh_seconds"] == 3600
        assert load_timer_settings(root) == settings
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_default_position_refresh_is_not_aggressive():
    root = temp_root()
    try:
        assert load_timer_settings(root)["positions_refresh_seconds"] == 30
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_monitor_should_skip_until_configured_interval_elapsed():
    root = temp_root()
    try:
        save_timer_settings({"monitor_interval_seconds": 60}, root)
        decisions = root / "data" / "trading" / "agent_decisions"
        decisions.mkdir(parents=True)
        latest = decisions / "position_decisions_1.csv"
        latest.write_text("symbol,decision\nAAA,hold\n", encoding="utf-8")
        now = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc) + timedelta(seconds=30)

        should_run, reason = monitor_should_run(root, now=now)
        assert should_run is False
        assert "below_configured_60s" in reason

        should_run, _ = monitor_should_run(root, now=now + timedelta(seconds=31))
        assert should_run is True
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_monitor_runs_when_no_previous_snapshot():
    root = temp_root()
    try:
        save_timer_settings({"monitor_interval_seconds": 60}, root)
        should_run, reason = monitor_should_run(root)
        assert should_run is True
        assert reason == "no_previous_decision_snapshot"
    finally:
        shutil.rmtree(root, ignore_errors=True)
