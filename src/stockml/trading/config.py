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
    min_intraday_volume: int
    min_market_cap: float
    min_risk_adjusted_score: float
    transaction_cost_bps: float
    account_equity: float = 33333.34
    max_position_pct: float = 0.03
    min_avg_dollar_volume_20d: float = 5_000_000.0
    allow_short_selling: bool = False
    min_expected_trade_return: float = 0.002
    live_trading_enabled: bool = False
    paper_trading_enabled: bool = True
    candidate_pool_size: int = 50
    directional_candidate_long_fraction: float = 0.70


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y"}


def _int_env(name: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        return max(minimum, value)
    return value


def _float_env(name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def alpaca_config() -> AlpacaConfig:
    _hydrate_environment()
    return AlpacaConfig(
        api_key=os.environ.get("ALPACA_API_KEY", "").strip(),
        secret_key=os.environ.get("ALPACA_SECRET_KEY", "").strip(),
        base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/"),
        submit_orders=_bool_env("STOCKML_ALPACA_SUBMIT_ORDERS", default=False),
        extended_hours=_bool_env("STOCKML_ALPACA_EXTENDED_HOURS", default=False),
        max_orders=_int_env("STOCKML_ALPACA_MAX_ORDERS", 10, minimum=1),
        max_notional_per_order=float(os.environ.get("STOCKML_ALPACA_MAX_NOTIONAL_PER_ORDER", "1000")),
        max_total_notional=float(os.environ.get("STOCKML_ALPACA_MAX_TOTAL_NOTIONAL", "10000")),
        min_trade_price=float(os.environ.get("STOCKML_ALPACA_MIN_TRADE_PRICE", "5")),
        max_sector_fraction=float(os.environ.get("STOCKML_ALPACA_MAX_SECTOR_FRACTION", "0.4")),
        min_side_probability=float(os.environ.get("STOCKML_ALPACA_MIN_SIDE_PROBABILITY", "0.55")),
        min_abs_probability_edge=float(os.environ.get("STOCKML_ALPACA_MIN_ABS_PROBABILITY_EDGE", "0.05")),
        min_intraday_volume=int(os.environ.get("STOCKML_ALPACA_MIN_INTRADAY_VOLUME", "100000")),
        min_market_cap=float(os.environ.get("STOCKML_ALPACA_MIN_MARKET_CAP", "300000000")),
        min_risk_adjusted_score=float(os.environ.get("STOCKML_ALPACA_MIN_RISK_ADJUSTED_SCORE", "0.005")),
        transaction_cost_bps=float(os.environ.get("STOCKML_ALPACA_TRANSACTION_COST_BPS", "10")),
        account_equity=float(os.environ.get("STOCKML_ACCOUNT_EQUITY", "33333.34")),
        max_position_pct=float(os.environ.get("STOCKML_MAX_POSITION_PCT", "0.03")),
        min_avg_dollar_volume_20d=float(os.environ.get("STOCKML_MIN_AVG_DOLLAR_VOLUME_20D", "5000000")),
        allow_short_selling=_bool_env("STOCKML_ALLOW_SHORT_SELLING", default=False),
        min_expected_trade_return=float(os.environ.get("STOCKML_MIN_EXPECTED_TRADE_RETURN", "0.002")),
        live_trading_enabled=_bool_env("STOCKML_LIVE_TRADING_ENABLED", default=False),
        paper_trading_enabled=_bool_env("STOCKML_PAPER_TRADING_ENABLED", default=True),
        candidate_pool_size=_int_env("STOCKML_CANDIDATE_POOL_SIZE", 50, minimum=1),
        directional_candidate_long_fraction=_float_env("STOCKML_DIRECTIONAL_CANDIDATE_LONG_FRACTION", 0.70, minimum=0.0, maximum=1.0),
    )
