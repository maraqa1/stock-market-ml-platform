from pathlib import Path

import pandas as pd

from scripts.diagnose_symbol_lifecycle import build_symbol_lifecycle, write_outputs


def test_symbol_lifecycle_diagnostic_reports_repeated_dftx_churn(tmp_path: Path, monkeypatch):
    trade_dir = tmp_path / "data" / "trading"
    trade_dir.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "position_id": "1",
                "symbol": "DFTX",
                "direction": "long",
                "opened_at": "2026-07-10T16:04:00+00:00",
                "closed_at": "2026-07-10T16:05:00+00:00",
                "entry_fill": 45.55,
                "exit_fill": 45.42,
                "realized_pnl_usd": -8.7,
                "close_reason": "EOD_FLATTEN",
            },
            {
                "position_id": "2",
                "symbol": "DFTX",
                "direction": "long",
                "opened_at": "2026-07-10T16:13:00+00:00",
                "closed_at": "2026-07-10T16:14:00+00:00",
                "entry_fill": 45.66,
                "exit_fill": 45.59,
                "realized_pnl_usd": -5.9,
                "close_reason": "EOD_FLATTEN",
            },
        ]
    ).to_csv(trade_dir / "closed_trades_attribution_20260710_161401.csv", index=False)
    monkeypatch.setattr("scripts.diagnose_symbol_lifecycle._open_log", lambda symbol, day: pd.DataFrame())
    frame = build_symbol_lifecycle("DFTX", "2026-07-10", root=tmp_path)
    assert len(frame) == 2
    assert int(frame["block_reason"].str.contains("eod_flatten_outside_window").sum()) == 2
    assert int((pd.to_numeric(frame["hold_minutes"]) < 30).sum()) == 2
    csv_path, md_path = write_outputs(frame, symbol="DFTX", root=tmp_path, stamp="test")
    assert csv_path.exists()
    assert md_path.exists()
    assert "churn_detected: yes" in md_path.read_text()
