from pathlib import Path

import scripts.run_trading_day_readiness as readiness


def test_trading_day_readiness_stops_before_plan_when_quality_fails(monkeypatch):
    calls = {"plan": 0}
    monkeypatch.setattr(readiness, "run_profile", lambda *args, **kwargs: None)
    monkeypatch.setattr(readiness, "audit_latest_pipeline", lambda *args, **kwargs: {"status": "ok", "manifest_path": "manifest.json"})
    monkeypatch.setattr(
        readiness,
        "build_pipeline_quality_report",
        lambda root: {
            "status": "failed",
            "path": "quality.csv",
            "failed_checks": 1,
            "failures": [{"check": "metadata_market_cap_coverage", "observed": 0.0, "threshold": ">=0.7", "message": "bad metadata"}],
        },
    )
    monkeypatch.setattr(readiness, "run_paper_trading", lambda *args, **kwargs: calls.__setitem__("plan", calls["plan"] + 1))

    assert readiness.main([]) == 1
    assert calls["plan"] == 0


def test_trading_day_readiness_creates_plan_and_holding_review_after_quality_passes(monkeypatch, tmp_path: Path):
    plan_path = tmp_path / "plan.csv"
    plan_path.write_text("symbol\nAAA\n", encoding="utf-8")
    monkeypatch.setattr(readiness, "run_profile", lambda *args, **kwargs: None)
    monkeypatch.setattr(readiness, "audit_latest_pipeline", lambda *args, **kwargs: {"status": "ok", "manifest_path": "manifest.json"})
    monkeypatch.setattr(
        readiness,
        "build_pipeline_quality_report",
        lambda root: {"status": "ok", "path": "quality.csv", "failed_checks": 0, "failures": []},
    )
    monkeypatch.setattr(
        readiness,
        "run_paper_trading",
        lambda *args, **kwargs: {
            "orders_planned": 1,
            "candidate_pool_rows": 100,
            "orders_approved": 1,
            "orders_rejected": 0,
            "plan_path": plan_path,
            "result_path": tmp_path / "results.csv",
        },
    )
    monkeypatch.setattr(
        readiness,
        "generate_holding_period_report",
        lambda root, plan_file: {
            "status": "ok",
            "review_rows": 1,
            "review_passed": 1,
            "review_blocked": 0,
            "review_path": tmp_path / "review.csv",
        },
    )

    assert readiness.main([]) == 0


def test_trading_day_readiness_stops_when_pipeline_doctor_fails(monkeypatch):
    calls = {"quality": 0}
    monkeypatch.setattr(readiness, "run_profile", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        readiness,
        "audit_latest_pipeline",
        lambda *args, **kwargs: {"status": "failed", "reason": "pipeline_stale_running", "manifest_path": "manifest.json"},
    )
    monkeypatch.setattr(
        readiness,
        "build_pipeline_quality_report",
        lambda root: calls.__setitem__("quality", calls["quality"] + 1),
    )

    assert readiness.main([]) == 1
    assert calls["quality"] == 0
