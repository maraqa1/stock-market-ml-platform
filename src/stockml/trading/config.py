from __future__ import annotations

import os
from dataclasses import dataclass

from stockml.db.connection import _hydrate_environment


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str
    secret_key: str
    base_url: str
    submit_orders: bool
    extended_hours: bool
    max_orders: int
    max_notional_per_order: float
    max_total_notional: float
    min_trade_price: float
    max_sector_fraction: float
    min_side_probability: float
    min_abs_probability_edge: float


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y"}


def alpaca_config() -> AlpacaConfig:
    _hydrate_environment()
    return AlpacaConfig(
        api_key=os.environ.get("ALPACA_API_KEY", "").strip(),
        secret_key=os.environ.get("ALPACA_SECRET_KEY", "").strip(),
        base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/"),
        submit_orders=_bool_env("STOCKML_ALPACA_SUBMIT_ORDERS", default=False),
        extended_hours=_bool_env("STOCKML_ALPACA_EXTENDED_HOURS", default=False),
        max_orders=int(os.environ.get("STOCKML_ALPACA_MAX_ORDERS", "10")),
        max_notional_per_order=float(os.environ.get("STOCKML_ALPACA_MAX_NOTIONAL_PER_ORDER", "1000")),
        max_total_notional=float(os.environ.get("STOCKML_ALPACA_MAX_TOTAL_NOTIONAL", "10000")),
        min_trade_price=float(os.environ.get("STOCKML_ALPACA_MIN_TRADE_PRICE", "5")),
        max_sector_fraction=float(os.environ.get("STOCKML_ALPACA_MAX_SECTOR_FRACTION", "0.4")),
        min_side_probability=float(os.environ.get("STOCKML_ALPACA_MIN_SIDE_PROBABILITY", "0.55")),
        min_abs_probability_edge=float(os.environ.get("STOCKML_ALPACA_MIN_ABS_PROBABILITY_EDGE", "0.05")),
    )
