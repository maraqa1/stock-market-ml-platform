from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from stockml.common.paths import PROJECT_ROOT
from stockml.trading.execution_owner import normalize_execution_owner


AUTOPILOT_CONFIG_PATH = PROJECT_ROOT / "config" / "autopilot.yaml"
OTHER_BRAIN_BLOCK_REASON = "daily_trading_single_brain_blocks_secondary_decision_path"


@dataclass(frozen=True)
class DailyTradingAuthority:
    enabled: bool = True
    decision_owner: str = "paper_autopilot"
    allow_auto_rotations: bool = False
    allow_fallback_candidate_brains: bool = False
    allow_legacy_basket_submit: bool = False

    @property
    def single_brain_active(self) -> bool:
        return bool(self.enabled and self.decision_owner == "paper_autopilot")


def _bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def load_daily_trading_authority(path: Path | str | None = AUTOPILOT_CONFIG_PATH) -> DailyTradingAuthority:
    payload: dict[str, Any] = {}
    config_path = Path(path) if path is not None else AUTOPILOT_CONFIG_PATH
    if config_path.exists():
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        payload = loaded if isinstance(loaded, dict) else {}
    section = payload.get("daily_trading_authority")
    section = section if isinstance(section, dict) else {}
    owner = normalize_execution_owner(section.get("decision_owner", payload.get("execution_owner", "paper_autopilot")))
    return DailyTradingAuthority(
        enabled=_bool(section.get("enabled"), True),
        decision_owner=owner,
        allow_auto_rotations=_bool(section.get("allow_auto_rotations"), False),
        allow_fallback_candidate_brains=_bool(section.get("allow_fallback_candidate_brains"), False),
        allow_legacy_basket_submit=_bool(section.get("allow_legacy_basket_submit"), False),
    )


def secondary_decision_path_allowed(path_name: str, authority: DailyTradingAuthority | None = None) -> tuple[bool, str]:
    active = authority or load_daily_trading_authority()
    if not active.single_brain_active:
        return True, ""
    clean = str(path_name or "").strip().lower()
    if clean in {"auto_rotation", "rotation"} and not active.allow_auto_rotations:
        return False, OTHER_BRAIN_BLOCK_REASON
    if clean in {"fallback_candidate_brain", "fallback_candidates"} and not active.allow_fallback_candidate_brains:
        return False, OTHER_BRAIN_BLOCK_REASON
    if clean in {"legacy_basket_submit", "legacy_paper_trader"} and not active.allow_legacy_basket_submit:
        return False, OTHER_BRAIN_BLOCK_REASON
    return True, ""
