from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from stockml.trading.snapshot_export import export_trading_snapshot


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
