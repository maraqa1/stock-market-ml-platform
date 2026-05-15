from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from stockml.common.paths import PROJECT_ROOT
from stockml.trading.per_symbol_forecast.generate import latest_per_symbol_forecast_path

AUDIT_DIR_NAME = "per_symbol_forecast_audit"


def audit_per_symbol_forecast(root: Path | None = None, stamp: str | None = None) -> dict[str, object]:
    base = Path(root).resolve() if root else PROJECT_ROOT
    latest = latest_per_symbol_forecast_path(base)
    frame = pd.read_csv(latest, low_memory=False) if latest and latest.exists() else pd.DataFrame()
    out_dir = base / "reports" / AUDIT_DIR_NAME
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = pd.DataFrame(
        [
            {
                "generated_at": generated_at,
                "source_file": latest.name if latest else "",
                "forecast_rows": int(len(frame)),
                "tier_b_expected_5d_return_bps_correlation": None,
                "tier_c_direction_probability_brier_score": None,
                "audit_status": "awaiting_realized_outcomes",
            }
        ]
    )
    day = stamp or datetime.now(timezone.utc).strftime("%Y%m%d")
    path = out_dir / f"per_symbol_forecast_audit_{day}.csv"
    report.to_csv(path, index=False)
    return {"status": "ok", "rows": int(len(report)), "path": str(path), "source": str(latest or "")}
