from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT


CONFIG_PATH = PROJECT_ROOT / "config" / "intraday.yaml"
MIN_CADENCE_MINUTES = 5


@dataclass(frozen=True)
class IntradayConfig:
    version: int
    enabled: bool
    shadow_only: bool
    cadence_minutes: int
    timeframe: str
    bar_limit: int
    provider: str
    reference_provider_enabled: bool = False


def load_intraday_config(path: Path | str = CONFIG_PATH) -> IntradayConfig:
    payload: dict[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cadence = int(payload.get("cadence_minutes", MIN_CADENCE_MINUTES))
    if cadence < MIN_CADENCE_MINUTES:
        raise ValueError(f"intraday cadence must be at least {MIN_CADENCE_MINUTES} minutes")
    return IntradayConfig(
        version=int(payload.get("version", 1)),
        enabled=bool(payload.get("enabled", True)),
        shadow_only=bool(payload.get("shadow_only", True)),
        cadence_minutes=cadence,
        timeframe=str(payload.get("timeframe", "5Min")),
        bar_limit=int(payload.get("bar_limit", 12)),
        provider=str(payload.get("provider", "alpaca")),
        reference_provider_enabled=bool(payload.get("reference_provider_enabled", False)),
    )

