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
        ("broker_fill_reconciliation", "run_broker_fill_reconciliation.py", []),
        ("candidate_trade_attribution", "run_candidate_trade_attribution.py", []),
        ("missed_better_candidates", "run_missed_better_candidates.py", []),
        ("position_management_outcomes", "run_position_management_outcomes.py", []),
        ("ranking_polarity", "run_ranking_polarity_diagnostic.py", []),
        ("side_mapping_audit", "run_side_mapping_audit.py", []),
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
        "--skip-fill-reconciliation",
        "--skip-candidate-trade-attribution",
        "--skip-missed-better-candidates",
        "--skip-position-management-outcomes",
        "--skip-ranking-polarity",
        "--skip-side-mapping-audit",
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


def test_default_steps_include_read_only_diagnostics():
    names = [name for name, _script in post.DEFAULT_STEPS]
    assert names[-6:] == [
        "broker_fill_reconciliation",
        "candidate_trade_attribution",
        "missed_better_candidates",
        "position_management_outcomes",
        "ranking_polarity",
        "side_mapping_audit",
    ]
