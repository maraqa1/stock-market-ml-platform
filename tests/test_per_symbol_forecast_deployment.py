from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_full_nightly_generates_per_symbol_forecast_after_order_plan():
    service = (ROOT / "deployment" / "systemd" / "stockml-full-nightly.service").read_text(encoding="utf-8")

    pipeline_index = service.index("scripts/run_profile_pipeline.py")
    plan_index = service.index("scripts/run_alpaca_paper_trader.py --plan-only")
    forecast_index = service.index("scripts/run_per_symbol_forecast.py")

    assert pipeline_index < plan_index < forecast_index


def test_intraday_clock_generates_per_symbol_forecast_before_autopilot():
    script = (ROOT / "scripts" / "run_intraday_trading_clock.py").read_text(encoding="utf-8")

    refresh_index = script.index("candidate_refresh_tick")
    scoring_index = script.index("score_unscored_snapshots")
    forecast_index = script.index("forecast = generate_per_symbol_forecast")
    autopilot_index = script.index("state = autopilot_tick")

    assert refresh_index < scoring_index < forecast_index < autopilot_index
