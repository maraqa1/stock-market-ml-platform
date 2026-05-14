from __future__ import annotations

import pandas as pd

from stockml.trading.per_symbol_forecast.schema import OUTPUT_COLUMNS


def validate_output(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.reindex(columns=OUTPUT_COLUMNS)
    if not out.empty and not out["diagnostic_only"].fillna(False).astype(bool).all():
        raise ValueError("per-symbol forecast rows must be diagnostic only")
    return out
