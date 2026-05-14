from __future__ import annotations

from stockml.trading.per_symbol_forecast.schema import null_tier_c_fields


def model_fields() -> dict[str, object]:
    return null_tier_c_fields()
