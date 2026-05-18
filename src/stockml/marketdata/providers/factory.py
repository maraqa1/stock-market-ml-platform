from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.marketdata.providers.base import MarketDataProvider
from stockml.marketdata.providers.eodhd import EodhdProvider
from stockml.marketdata.providers.yahoo_legacy import YahooLegacyProvider


CONFIG_PATH = PROJECT_ROOT / "config" / "marketdata.yaml"


def load_marketdata_config(path: Path | str = CONFIG_PATH) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    section = payload.get("marketdata") if isinstance(payload, dict) else {}
    return section if isinstance(section, dict) else {}


def configured_provider_name(path: Path | str = CONFIG_PATH) -> str:
    env_value = os.getenv("STOCKML_MARKETDATA_PROVIDER", "").strip()
    if env_value:
        return env_value.lower()
    return str(load_marketdata_config(path).get("primary_provider", "yahoo_legacy")).lower().strip()


def provider_from_name(name: str | None = None, **kwargs: Any) -> MarketDataProvider:
    clean = str(name or configured_provider_name()).lower().strip()
    if clean in {"yahoo", "yahoo_legacy", "yfinance"}:
        return YahooLegacyProvider()
    if clean in {"eodhd", "eod"}:
        return EodhdProvider(**kwargs)
    raise ValueError(f"Unsupported market data provider: {name}")
