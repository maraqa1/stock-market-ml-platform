from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from stockml.common.paths import PROJECT_ROOT, timestamp


DIAGNOSTIC_DIR = PROJECT_ROOT / "data" / "trading" / "diagnostics"


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if pd.isna(parsed):
            return None
        return parsed
    except Exception:
        return None


def _forward_return_bps(row: pd.Series) -> float | None:
    for column in ["forward_return_bps", "forward_5d_return_bps", "realised_forward_return_bps", "realized_forward_return_bps"]:
        value = _num(row.get(column))
        if value is not None:
            return value
    for column in ["forward_5d_return", "forward_return", "realised_forward_return", "realized_forward_return"]:
        value = _num(row.get(column))
        if value is not None:
            return value * 10_000 if abs(value) <= 2 else value
    return None


def build_short_inverse_shadow(candidates: pd.DataFrame, *, estimated_cost_bps: float = 0.0) -> pd.DataFrame:
    if candidates is None or candidates.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "original_side",
                "status",
                "primary_block_reason",
                "forward_return_bps",
                "original_short_return_bps",
                "inverse_long_return_bps",
                "estimated_cost_bps",
                "inverse_after_cost_bps",
                "shadow_only",
            ]
        )
    rows: list[dict[str, Any]] = []
    for _, row in candidates.iterrows():
        side = _text(row.get("side")).lower()
        action = _text(row.get("trade_action") or row.get("source_trade_action")).lower()
        if side not in {"sell", "short"} and action != "short":
            continue
        forward = _forward_return_bps(row)
        original_short = -forward if forward is not None else None
        inverse_long = forward
        rows.append(
            {
                "symbol": (_text(row.get("symbol")) or _text(row.get("ticker"))).upper(),
                "original_side": "short",
                "status": row.get("status", row.get("candidate_status", "")),
                "primary_block_reason": row.get("primary_block_reason", row.get("trade_quality_reason", "")),
                "forward_return_bps": forward,
                "original_short_return_bps": original_short,
                "inverse_long_return_bps": inverse_long,
                "estimated_cost_bps": estimated_cost_bps,
                "inverse_after_cost_bps": inverse_long - estimated_cost_bps if inverse_long is not None else None,
                "shadow_only": True,
            }
        )
    return pd.DataFrame(rows)


def write_short_inverse_shadow(
    candidates: pd.DataFrame,
    *,
    output_dir: Path | str | None = None,
    stamp: str | None = None,
    estimated_cost_bps: float = 0.0,
) -> Path:
    out_dir = Path(output_dir) if output_dir else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = stamp or timestamp()
    path = out_dir / f"short_inverse_shadow_{run_stamp}.csv"
    build_short_inverse_shadow(candidates, estimated_cost_bps=estimated_cost_bps).to_csv(path, index=False)
    return path
