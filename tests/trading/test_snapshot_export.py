from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from stockml.trading.snapshot_export import _dedupe_near_miss_snapshot_rows, export_trading_snapshot
from stockml.trading import snapshot_export


def test_export_trading_snapshot_writes_timestamped_csv(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "stockml.trading.snapshot_export.snapshot_pools",
        lambda root: [("model_shortlist", [{"symbol": "AAA", "side": "buy", "risk_adjusted_score": 0.5}], "", "fixture")],
    )

    result = export_trading_snapshot(tmp_path, stamp="20260515_120000", snapshot_at=datetime(2026, 5, 15, 12, tzinfo=timezone.utc))
    path = Path(result["path"])

    assert result["status"] == "ok"
    assert result["rows"] == 1
    assert path.name == "trading_snapshot_20260515_120000.csv"
    assert path.read_text(encoding="utf-8").splitlines()[0].find("score_state") >= 0


def test_near_miss_snapshot_rows_are_deduped_on_visible_quality_key():
    rows = [
        {"symbol": "AAA", "side": "buy", "status": "rejected", "risk_adjusted_score": 0.1, "failed_gate": "risk_adjusted_score_below_threshold"},
        {"symbol": "AAA", "side": "buy", "status": "rejected", "risk_adjusted_score": 0.1, "failed_gate": "market_cap_below_minimum"},
        {"symbol": "AAA", "side": "buy", "status": "rejected", "risk_adjusted_score": 0.2, "failed_gate": "market_cap_below_minimum"},
    ]

    result = _dedupe_near_miss_snapshot_rows(rows)

    assert len(result) == 2
    assert [row["risk_adjusted_score"] for row in result] == [0.1, 0.2]


def test_snapshot_pools_requests_full_intraday_promotion_audit_rows(monkeypatch, tmp_path: Path):
    requested: dict[str, int | None] = {}

    monkeypatch.setattr(snapshot_export, "trading_context", lambda root: {})
    monkeypatch.setattr(snapshot_export, "positions_context", lambda root: {})
    monkeypatch.setattr(snapshot_export, "action_queue_context", lambda root: {})
    monkeypatch.setattr(snapshot_export, "near_miss_context", lambda root: {})
    monkeypatch.setattr(snapshot_export, "per_symbol_forecast_context", lambda root, limit=1000: {})

    def fake_promotions(root: Path, *, limit: int | None = 20) -> dict:
        requested["limit"] = limit
        return {"rows": [{"symbol": "SHRT", "nightly_bias": "short", "promotion_score": 0.1}], "latest_tick": "fixture"}

    monkeypatch.setattr(snapshot_export, "intraday_promotion_context", fake_promotions)

    pools = snapshot_export.snapshot_pools(tmp_path)

    assert requested["limit"] is None
    assert ("intraday_promotion", [{"symbol": "SHRT", "nightly_bias": "short", "promotion_score": 0.1}], "fixture", "promotion_log") in pools
