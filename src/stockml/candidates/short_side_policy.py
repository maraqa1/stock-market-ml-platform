from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT


@dataclass(frozen=True)
class ShortSidePolicy:
    enabled: bool = False
    allow_shorts_in_validation: bool = False
    require_short_side_attribution_pass: bool = True
    research_only_when_disabled: bool = True


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def load_short_side_policy(path: Path | str | None = None) -> ShortSidePolicy:
    config_path = Path(path) if path else PROJECT_ROOT / "config" / "trading.yaml"
    payload: dict[str, Any] = {}
    if config_path.exists():
        try:
            payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            payload = {}
    data = payload.get("short_side_policy", {}) if isinstance(payload, dict) else {}
    return ShortSidePolicy(
        enabled=_bool(data.get("enabled"), False),
        allow_shorts_in_validation=_bool(data.get("allow_shorts_in_validation"), False),
        require_short_side_attribution_pass=_bool(data.get("require_short_side_attribution_pass"), True),
        research_only_when_disabled=_bool(data.get("research_only_when_disabled"), True),
    )


def short_side_block_reason(row: Any, policy: ShortSidePolicy | None = None) -> str:
    active_policy = policy or load_short_side_policy()
    side = str(row.get("side", "") if hasattr(row, "get") else "").strip().lower()
    action = str(row.get("trade_action", "") if hasattr(row, "get") else "").strip().lower()
    is_short = side == "sell" or action == "short"
    if not is_short:
        return ""
    if active_policy.enabled and active_policy.allow_shorts_in_validation:
        return ""
    if active_policy.research_only_when_disabled:
        return "short_side_validation_required"
    return "short_side_disabled"
