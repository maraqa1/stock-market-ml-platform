from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_intraday_trading_clock_systemd_units_are_utc_and_synchronized():
    service = (ROOT / "deployment" / "systemd" / "stockml-intraday-trading-clock.service").read_text(encoding="utf-8")
    timer = (ROOT / "deployment" / "systemd" / "stockml-intraday-trading-clock.timer").read_text(encoding="utf-8")

    assert "scripts/run_intraday_trading_clock.py" in service
    assert "Environment=PYTHONPATH=src" in service
    assert "OnCalendar=Mon..Fri *-*-* 00..23:0/5:00 UTC" in timer
    assert "Unit=stockml-intraday-trading-clock.service" in timer


def test_intraday_trading_clock_script_runs_pipeline_in_order():
    script = (ROOT / "scripts" / "run_intraday_trading_clock.py").read_text(encoding="utf-8")

    refresh_index = script.index("candidate_refresh_tick")
    scoring_index = script.index("score_unscored_snapshots")
    forecast_index = script.index("forecast = generate_per_symbol_forecast")
    rotation_index = script.index("run_rotation_recommendations()")
    autopilot_index = script.index("state = autopilot_tick")
    snapshot_index = script.index("snapshot = export_trading_snapshot")

    assert refresh_index < scoring_index < forecast_index < rotation_index < autopilot_index < snapshot_index


def test_intraday_trading_clock_allows_overnight_auto_open_when_market_closed():
    script = (ROOT / "scripts" / "run_intraday_trading_clock.py").read_text(encoding="utf-8")

    assert "trading_cfg.overnight_trading_enabled" in script
    assert "overnight_enabled_market_closed" in script
    assert "autopilot_tick(allow_auto_open=allow_auto_open)" in script


def test_intraday_trading_clock_exports_snapshot_after_autopilot():
    script = (ROOT / "scripts" / "run_intraday_trading_clock.py").read_text(encoding="utf-8")

    assert "from stockml.trading.snapshot_export import export_trading_snapshot" in script
    assert "trading_snapshot_path:" in script


def test_intraday_trading_clock_rearms_benign_completed_autopilot():
    script = (ROOT / "scripts" / "run_intraday_trading_clock.py").read_text(encoding="utf-8")

    assert "load_autopilot_state" in script
    assert "start_autopilot()" in script
    assert "paper_autopilot_rearm:" in script
    assert "autopilot_not_running" in script
    assert "autopilot_error" in script


def test_intraday_clock_allows_overnight_auto_open_gate():
    script = Path("scripts/run_intraday_trading_clock.py").read_text()
    assert "overnight_enabled_market_closed" in script
    assert "trading_cfg.overnight_trading_enabled" in script
