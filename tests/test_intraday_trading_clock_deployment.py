from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_intraday_trading_clock_systemd_units_are_utc_and_synchronized():
    service = (ROOT / "deployment" / "systemd" / "stockml-intraday-trading-clock.service").read_text(encoding="utf-8")
    timer = (ROOT / "deployment" / "systemd" / "stockml-intraday-trading-clock.timer").read_text(encoding="utf-8")

    assert "scripts/run_intraday_trading_clock.py" in service
    assert "Environment=PYTHONPATH=src" in service
    assert "OnCalendar=Mon..Fri *-*-* 13..21:0/5:00 UTC" in timer
    assert "Unit=stockml-intraday-trading-clock.service" in timer


def test_intraday_trading_clock_script_runs_pipeline_in_order():
    script = (ROOT / "scripts" / "run_intraday_trading_clock.py").read_text(encoding="utf-8")

    refresh_index = script.index("candidate_refresh_tick")
    scoring_index = script.index("score_unscored_snapshots")
    rotation_index = script.index("run_rotation_recommendations()")
    autopilot_index = script.index('autopilot_action("tick")')

    assert refresh_index < scoring_index < rotation_index < autopilot_index
