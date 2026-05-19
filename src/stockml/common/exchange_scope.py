from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd


def normalize_exchanges(exchange: Any = None, exchanges: Any = None) -> list[str]:
    raw = exchanges if exchanges is not None else exchange
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        values = [part.strip() for part in raw.split(",")]
    elif isinstance(raw, Iterable):
        values = [str(part).strip() for part in raw]
    else:
        values = [str(raw).strip()]
    return [value.upper() for value in values if value]


def exchange_scope_label(exchange: Any = None, exchanges: Any = None) -> str:
    values = normalize_exchanges(exchange, exchanges)
    return ",".join(values) if values else "ALL"


def filter_listing_exchange(frame: pd.DataFrame, exchange: Any = None, exchanges: Any = None, *, column: str = "listing_exchange") -> pd.DataFrame:
    values = normalize_exchanges(exchange, exchanges)
    if not values or column not in frame.columns:
        return frame
    clean = frame[column].astype(str).str.upper().str.strip()
    return frame[clean.isin(set(values))].copy()

