from __future__ import annotations

from typing import Any

LEGACY_BLOCK_REASON = "legacy_submitter_blocked_by_paper_autopilot_owner"
VALID_EXECUTION_OWNERS = {"paper_autopilot", "legacy_paper_trader", "plan_only"}


def normalize_execution_owner(value: Any) -> str:
    owner = str(value or "paper_autopilot").strip().lower()
    return owner if owner in VALID_EXECUTION_OWNERS else "paper_autopilot"


def legacy_paper_trader_can_submit(config: Any) -> tuple[bool, str]:
    from stockml.trading.daily_trading_authority import secondary_decision_path_allowed

    authority_allowed, authority_reason = secondary_decision_path_allowed("legacy_basket_submit")
    if not authority_allowed:
        return False, authority_reason
    owner = normalize_execution_owner(getattr(config, "execution_owner", "paper_autopilot"))
    if owner != "legacy_paper_trader":
        return False, LEGACY_BLOCK_REASON
    return True, ""
