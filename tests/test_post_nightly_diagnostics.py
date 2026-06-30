from pathlib import Path

import pytest

import scripts.run_post_nightly_diagnostics as post


def test_wait_for_pipeline_returns_when_doctor_ok(monkeypatch):
    calls = [
        {"status": "running", "reason": "pipeline_running", "missing_stages": ["trading_day_readiness"]},
        {"status": "ok", "reason": "", "missing_stages": []},
    ]
    monkeypatch.setattr(post, "audit_latest_pipeline", lambda *args, **kwargs: calls.pop(0))
    monkeypatch.setattr(post.time, "sleep", lambda seconds: None)

    result = post.wait_for_pipeline(profile="us_full", stale_after_minutes=360, max_wait_minutes=5, poll_seconds=1)

    assert result["status"] == "ok"


def test_wait_for_pipeline_raises_on_failed_doctor(monkeypatch):
    monkeypatch.setattr(
        post,
        "audit_latest_pipeline",
        lambda *args, **kwargs: {"status": "failed", "reason": "artifact_missing", "missing_stages": []},
    )

    with pytest.raises(RuntimeError, match="pipeline_doctor_failed:artifact_missing"):
        post.wait_for_pipeline(profile="us_full", stale_after_minutes=360, max_wait_minutes=5, poll_seconds=1)


def test_main_runs_selected_steps_after_ok_doctor(monkeypatch):
    ran = []
    monkeypatch.setattr(
        post,
        "audit_latest_pipeline",
        lambda *args, **kwargs: {"status": "ok", "reason": "", "missing_stages": []},
    )
    monkeypatch.setattr(post, "run_step", lambda name, script, extra_args=None: ran.append((name, Path(script).name, extra_args or [])))

    code = post.main(["--skip-wait", "--skip-promotion-replay", "--diagnostic-date", "2026-06-30"])

    assert code == 0
    assert ran == [
        ("trade_ledger", "build_trade_ledger.py", ["--date", "2026-06-30"]),
        ("profitability_attribution", "build_profitability_attribution.py", []),
        ("strategy_diagnostics", "run_strategy_diagnostics.py", []),
    ]


def test_main_can_skip_trade_ledger_and_attribution(monkeypatch):
    ran = []
    monkeypatch.setattr(
        post,
        "audit_latest_pipeline",
        lambda *args, **kwargs: {"status": "ok", "reason": "", "missing_stages": []},
    )
    monkeypatch.setattr(post, "run_step", lambda name, script, extra_args=None: ran.append((name, Path(script).name, extra_args or [])))

    code = post.main([
        "--skip-wait",
        "--skip-trade-ledger",
        "--skip-profitability-attribution",
        "--skip-promotion-replay",
    ])

    assert code == 0
    assert ran == [("strategy_diagnostics", "run_strategy_diagnostics.py", [])]


def test_deployment_timer_references_post_nightly_script():
    service = (Path(__file__).resolve().parents[1] / "deployment" / "systemd" / "stockml-post-nightly-diagnostics.service").read_text(
        encoding="utf-8"
    )
    timer = (Path(__file__).resolve().parents[1] / "deployment" / "systemd" / "stockml-post-nightly-diagnostics.timer").read_text(
        encoding="utf-8"
    )

    assert "scripts/run_post_nightly_diagnostics.py" in service
    assert "After=stockml-full-nightly.service" in service
    assert "OnCalendar=*-*-* 10:00:00" in timer
